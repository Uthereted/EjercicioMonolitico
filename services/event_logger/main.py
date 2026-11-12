"""Dedicated, idempotent MongoDB archive of business facts and saga messages."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pymongo import MongoClient
from pymongo.write_concern import WriteConcern
from my_chassis import configure_logging
from my_chassis.events import decode_event
from my_chassis.messaging import TopicTransport

logger = logging.getLogger(__name__)
TOPICS = ('client.#', 'order.#', 'payment.#', 'piece.#', 'delivery.#')
SAGA_EXCHANGES = ('manufacturing.commands', 'manufacturing.saga.responses')


def store_event(collection, envelope):
    if not envelope['event_type'].startswith(('client.', 'order.', 'payment.', 'piece.', 'delivery.', 'saga.')):
        raise ValueError('Not a business event')
    collection.update_one({'_id': envelope['event_id']}, {'$setOnInsert': {
        'event_type': envelope['event_type'],
        'producer': envelope.get('producer', 'unknown'),
        'data': envelope['data'],
        'received_at': datetime.now(timezone.utc),
    }}, upsert=True)


async def process_event(message, collection):
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


async def run():
    configure_logging('event-logger')
    mongo = MongoClient(os.getenv('MONGODB_URL', 'mongodb://mongodb:27017'),
                        serverSelectionTimeoutMS=5000, socketTimeoutMS=5000, connectTimeoutMS=5000)
    collection = mongo[os.getenv('AUDIT_DATABASE', 'manufacturing_audit')]['business_events'].with_options(
        write_concern=WriteConcern(w=1, j=True))
    transport = TopicTransport(
        os.getenv('RABBITMQ_URL', 'amqp://manufacturing:manufacturing@localhost:5672/'),
        os.getenv('RABBITMQ_EXCHANGE', 'manufacturing.events'))
    try:
        while True:
            try:
                await asyncio.to_thread(collection.create_index, [('event_type', 1), ('received_at', -1)])
                await asyncio.to_thread(collection.create_index, [('data.order_id', 1), ('received_at', 1)])
                connection = await transport.connect()
                async with connection:
                    channel = await connection.channel()
                    await channel.set_qos(prefetch_count=20)
                    queue = await subscribe_audit(channel, transport)
                    logger.info('Business event audit consumer ready')
                    async with queue.iterator() as messages:
                        async for message in messages:
                            await process_event(message, collection)
            except Exception:
                logger.exception('Business event audit unavailable; retrying')
                await asyncio.sleep(2)
    finally:
        mongo.close()


@asynccontextmanager
async def lifespan(app):
    app.state.worker = asyncio.create_task(run())
    try:
        yield
    finally:
        app.state.worker.cancel()
        with suppress(asyncio.CancelledError):
            await app.state.worker


app = FastAPI(title="Event Logger Health", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.get('/service/health')
async def health():
    if app.state.worker.done():
        raise HTTPException(status_code=503, detail='Audit worker stopped')
    return {'status': 'ok', 'service': 'event-logger'}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
