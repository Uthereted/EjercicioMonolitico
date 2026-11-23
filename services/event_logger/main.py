from my_chassis.discovery import registered_service
from my_chassis.health import HealthMonitor, mongodb_ready
"""Dedicated, idempotent MongoDB archive of business facts and saga messages."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pymongo import MongoClient
from pymongo.write_concern import WriteConcern
from service_logging import install_logging, runtime
from my_chassis.events import decode_event
from my_chassis.messaging import TopicTransport

logger = logging.getLogger(__name__)
TOPICS = ('client.#', 'order.#', 'payment.#', 'piece.#', 'delivery.#')
SAGA_EXCHANGES = ('manufacturing.commands', 'manufacturing.saga.responses')
SAGA_HISTORY_EVENT = 'order.saga.state_changed'
audit_link = None


def audit_connected():
    return (audit_link is not None and not audit_link[0].is_closed
            and not audit_link[1].is_closed)


health_monitor = HealthMonitor({
    'mongodb': lambda: mongodb_ready(app.state.mongo),
    'rabbitmq': audit_connected,
    'logging': lambda: runtime.available,
    'worker': lambda: not app.state.worker.done(),
})


def saga_history_entry(envelope):
    data = envelope['data']
    for field in ('saga_id', 'order_id', 'sequence'):
        if type(data.get(field)) is not int or data[field] <= 0:
            raise ValueError(f'Invalid saga history {field}')
    if not isinstance(data.get('state'), str) or not 1 <= len(data['state']) <= 64:
        raise ValueError('Invalid saga state')
    previous = data.get('previous_state')
    if previous is not None and (not isinstance(previous, str) or not 1 <= len(previous) <= 64):
        raise ValueError('Invalid previous saga state')
    if type(data.get('initial_snapshot')) is not bool:
        raise ValueError('Invalid snapshot flag')
    timestamp = datetime.fromisoformat(data['timestamp'])
    if timestamp.tzinfo is None:
        raise ValueError('Saga history timestamp must include a timezone')
    expected_id = f"{SAGA_HISTORY_EVENT}:{data['saga_id']}:{data['sequence']}"
    if envelope.get('producer') != 'order-service' or envelope['event_id'] != expected_id:
        raise ValueError('Invalid saga history producer or identifier')
    return {'_id': envelope['event_id'], 'saga_id': data['saga_id'],
            'order_id': data['order_id'], 'state': data['state'],
            'previous_state': previous, 'sequence': data['sequence'],
            'timestamp': timestamp.astimezone(timezone.utc),
            'initial_snapshot': data['initial_snapshot'],
            'received_at': datetime.now(timezone.utc)}


def store_event(collection, envelope):
    if not envelope['event_type'].startswith(('client.', 'order.', 'payment.', 'piece.', 'delivery.', 'saga.')):
        raise ValueError('Not a business event')
    entry = saga_history_entry(envelope) if envelope['event_type'] == SAGA_HISTORY_EVENT else None
    collection.update_one({'_id': envelope['event_id']}, {'$setOnInsert': {
        'event_type': envelope['event_type'],
        'producer': envelope.get('producer', 'unknown'),
        'data': envelope['data'],
        'received_at': datetime.now(timezone.utc),
    }}, upsert=True)
    if entry is not None:
        history = collection.database.get_collection('saga_history', write_concern=WriteConcern(w=1, j=True))
        history.update_one({'_id': entry['_id']}, {'$setOnInsert': entry}, upsert=True)


async def process_event(message, collection):
    await runtime.wait_available('Archiving business event')
    try:
        envelope = decode_event(message.body, message.routing_key)
        await asyncio.to_thread(store_event, collection, envelope)
    except (ValueError, KeyError, TypeError):
        logger.exception('Invalid audit event; rejecting')
        await message.reject(requeue=False)
    except Exception:
        logger.exception('Audit persistence pending; requeueing')
        await asyncio.sleep(1)
        await message.nack(requeue=True)
    else:
        await message.ack()


async def subscribe_audit(channel, transport):
    queue = await transport.subscribe(channel, 'audit.domain', TOPICS)
    for exchange_name in SAGA_EXCHANGES:
        saga_transport = TopicTransport(transport.broker_url, exchange_name)
        await saga_transport.subscribe(channel, 'audit.domain', ('saga.#',))
    return queue


async def run(mongo):
    global audit_link
    collection = mongo[os.getenv('AUDIT_DATABASE', 'manufacturing_audit')]['business_events'].with_options(
        write_concern=WriteConcern(w=1, j=True))
    transport = TopicTransport(
        os.getenv('RABBITMQ_URL', 'amqps://manufacturing:manufacturing@localhost:5671/'),
        os.getenv('RABBITMQ_EXCHANGE', 'manufacturing.events'))
    try:
        while True:
            try:
                await asyncio.to_thread(collection.create_index, [('event_type', 1), ('received_at', -1)])
                await asyncio.to_thread(collection.create_index, [('data.order_id', 1), ('received_at', 1)])
                history = collection.database.get_collection('saga_history', write_concern=WriteConcern(w=1, j=True))
                await asyncio.to_thread(history.create_index, [('saga_id', 1), ('sequence', -1)], unique=True)
                await asyncio.to_thread(history.create_index, [('order_id', 1), ('sequence', 1)])
                await asyncio.to_thread(history.create_index, [('timestamp', -1)])
                connection = await transport.connect()
                async with connection:
                    channel = await connection.channel()
                    await channel.set_qos(prefetch_count=20)
                    queue = await subscribe_audit(channel, transport)
                    logger.info('Business event audit consumer ready')
                    async with queue.iterator() as messages:
                        audit_link = (connection, channel)
                        async for message in messages:
                            await process_event(message, collection)
            except Exception:
                logger.exception('Business event audit unavailable; retrying')
                await asyncio.sleep(2)
            finally:
                audit_link = None
    finally:
        mongo.close()


@asynccontextmanager
async def lifespan(app):
    app.state.mongo = MongoClient(os.getenv('MONGODB_URL', 'mongodb://mongodb:27017/?tls=true'),
                                 serverSelectionTimeoutMS=5000, socketTimeoutMS=5000,
                                 connectTimeoutMS=5000)
    app.state.worker = asyncio.create_task(run(app.state.mongo))
    try:
        async with health_monitor.lifespan(), registered_service("event-logger"):
            yield
    finally:
        app.state.worker.cancel()
        with suppress(asyncio.CancelledError):
            await app.state.worker


app = FastAPI(title="Event Logger Health", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
install_logging(app, 'event-logger')


@app.get('/service/health')
async def health():
    if not health_monitor.available:
        raise HTTPException(status_code=503, detail='Audit dependencies unavailable')
    return {'status': 'ok', 'service': 'event-logger'}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000, log_config=None, access_log=False,
                ssl_certfile=os.getenv("TLS_CERT_FILE"), ssl_keyfile=os.getenv("TLS_KEY_FILE"))
