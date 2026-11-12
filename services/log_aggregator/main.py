"""Store application messages from a dedicated exchange; query them as an admin."""
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
import json
import logging
import os
from typing import Literal

import aio_pika
from fastapi import Depends, FastAPI, HTTPException, Query
from my_chassis.auth import JWTVerifier
from my_chassis.events import decode_event
from my_chassis.keys import validate_public_key
from pymongo import MongoClient, DESCENDING
from pymongo.write_concern import WriteConcern

EXCHANGE = 'manufacturing.logs'
QUEUE = 'application.logs'
SERVICES = ('orders', 'payments', 'clients', 'machine', 'deliveries', 'event-logger')
LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')

mongo = MongoClient(os.getenv('MONGODB_URL', 'mongodb://mongodb:27017'),
                    serverSelectionTimeoutMS=1000, socketTimeoutMS=1000,
                    connectTimeoutMS=1000, tz_aware=True)
db = mongo[os.getenv('LOG_DATABASE', 'manufacturing_audit')]
logs = db.get_collection('application_logs', write_concern=WriteConcern(w=1, j=True))
keys = db.get_collection('log_auth_keys', write_concern=WriteConcern(w=1, j=True))
ready = False


async def load_public_key():
    row = await asyncio.to_thread(keys.find_one, {'_id': 'client'})
    return row['pem'] if row else None


verifier = JWTVerifier(key_loader=load_public_key, issuer='manufacturing-client',
                       audience='manufacturing-services')


async def require_admin(payload=Depends(verifier.verify_jwt)):
    if 'admin' not in payload.get('roles', []):
        raise HTTPException(403, 'Administrator role required')
    return payload


def parse_log(body, routing_key):
    entry = json.loads(body)
    if not isinstance(entry, dict):
        raise ValueError('Expected a log object')
    if entry.get('service') not in SERVICES or entry.get('level') not in LEVELS:
        raise ValueError('Invalid service or severity')
    if routing_key != f"{entry['service']}.{entry['level'].lower()}":
        raise ValueError('Routing key does not match log')
    if not isinstance(entry.get('log_id'), str) or not entry['log_id']:
        raise ValueError('Missing log ID')
    if not isinstance(entry.get('message'), str):
        raise ValueError('Missing message')
    timestamp = datetime.fromisoformat(entry['timestamp'])
    if timestamp.tzinfo is None:
        raise ValueError('Log timestamp must include a timezone')
    return {'_id': entry['log_id'], 'timestamp': timestamp.astimezone(timezone.utc),
            'service': entry['service'], 'level': entry['level'],
            'message': entry['message'], 'routing_key': routing_key,
            'logger': entry.get('logger'), 'file': entry.get('file'),
            'line': entry.get('line'), 'exception': entry.get('exception'),
            'received_at': datetime.now(timezone.utc)}


async def consume_log(message):
    global ready
    try:
        entry = parse_log(message.body, message.routing_key)
    except (ValueError, KeyError, TypeError):
        await message.reject(requeue=False)
        return
    try:
        await asyncio.to_thread(logs.update_one, {'_id': entry['_id']},
                                {'$setOnInsert': entry}, upsert=True)
    except Exception:
        ready = False
        await asyncio.sleep(1)
        await message.nack(requeue=True)
    else:
        # Acknowledge only after MongoDB has durably stored the log.
        await message.ack()


async def consume_key(message):
    try:
        event = decode_event(message.body, message.routing_key)
        pem = validate_public_key(event['data']['public_key'])
    except (ValueError, KeyError, TypeError):
        await message.reject(requeue=False)
        return
    try:
        await asyncio.to_thread(keys.replace_one, {'_id': 'client'},
                                {'_id': 'client', 'pem': pem}, upsert=True)
        await verifier.fetch_public_key()
    except Exception:
        await asyncio.sleep(1)
        await message.nack(requeue=True)
    else:
        await message.ack()


async def run():
    global ready
    while True:
        connection = None
        try:
            await asyncio.to_thread(logs.create_index,
                                    [('service', 1), ('level', 1), ('timestamp', DESCENDING)])
            await asyncio.to_thread(logs.create_index, [('timestamp', DESCENDING), ('_id', DESCENDING)])
            connection = await aio_pika.connect(
                os.getenv('RABBITMQ_URL', 'amqp://manufacturing:manufacturing@rabbitmq:5672/'),
                timeout=2,
            )
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=20)
            exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
            queue = await channel.declare_queue(QUEUE, durable=True)
            for service in SERVICES:
                await queue.bind(exchange, routing_key=f'{service}.*')
            await queue.consume(consume_log)
            # This subscription carries only the existing public key, never log messages.
            domain = await channel.declare_exchange('manufacturing.events', aio_pika.ExchangeType.TOPIC, durable=True)
            key_queue = await channel.declare_queue('logs.auth', durable=True)
            await key_queue.bind(domain, routing_key='auth.signing-key.published')
            await key_queue.consume(consume_key)
            while not connection.is_closed and not channel.is_closed:
                await asyncio.to_thread(mongo.admin.command, 'ping')
                # A write probe also detects a read-only/unwritable database.
                await asyncio.to_thread(db.log_status.update_one, {'_id': 'health'},
                                        {'$set': {'checked_at': datetime.now(timezone.utc)}}, upsert=True)
                ready = True
                await asyncio.sleep(1)
        except Exception:
            ready = False
            await asyncio.sleep(1)
        finally:
            ready = False
            if connection is not None:
                await connection.close()


@asynccontextmanager
async def lifespan(app):
    # Aggregator transport errors cannot recursively log through its own pipeline.
    logging.getLogger().handlers[:] = [logging.NullHandler()]
    logging.lastResort = logging.NullHandler()
    for logger in list(logging.Logger.manager.loggerDict.values()):
        if isinstance(logger, logging.Logger):
            logger.handlers.clear()
            logger.propagate = True
    task = asyncio.create_task(run())
    app.state.worker = task
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        mongo.close()


app = FastAPI(title='Log Aggregation Service', lifespan=lifespan)


@app.get('/service/health')
async def health():
    if not ready or app.state.worker.done():
        raise HTTPException(503, 'Log storage or broker unavailable')
    return {'status': 'ok', 'service': 'log-aggregator'}


@app.get('/logs', dependencies=[Depends(require_admin)])
async def query_logs(
    service: Literal['orders', 'payments', 'clients', 'machine', 'deliveries', 'event-logger'] | None = None,
    level: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    if not ready:
        raise HTTPException(503, 'Log storage or broker unavailable')
    for value in (date_from, date_to):
        if value is not None and value.tzinfo is None:
            raise HTTPException(422, 'Dates must include a timezone, e.g. 2026-11-12T00:00:00Z')
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, 'date_from must be before date_to')
    query = {}
    if service:
        query['service'] = service
    if level:
        query['level'] = level
    if date_from or date_to:
        query['timestamp'] = {}
        if date_from:
            query['timestamp']['$gte'] = date_from
        if date_to:
            query['timestamp']['$lte'] = date_to

    def read():
        items = list(logs.find(query).sort([('timestamp', DESCENDING), ('_id', DESCENDING)])
                     .skip(offset).limit(limit))
        for item in items:
            item['log_id'] = item.pop('_id')
        return {'items': items, 'total': logs.count_documents(query), 'limit': limit, 'offset': offset}

    try:
        return await asyncio.to_thread(read)
    except Exception as exc:
        raise HTTPException(503, 'Log database unavailable') from exc
