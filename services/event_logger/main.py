"""Dedicated, idempotent MongoDB archive of business facts."""
import asyncio
import logging
import os
from datetime import datetime, timezone

from pymongo import MongoClient
from pymongo.write_concern import WriteConcern
from my_chassis import configure_logging
from my_chassis.events import decode_event
from my_chassis.messaging import TopicTransport

logger = logging.getLogger(__name__)
TOPICS = ('client.#', 'order.#', 'payment.#', 'piece.#', 'delivery.#')


def store_event(collection, envelope):
    if not envelope['event_type'].startswith(('client.', 'order.', 'payment.', 'piece.', 'delivery.')):
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
                    queue = await transport.subscribe(channel, 'audit.domain', TOPICS)
                    logger.info('Business event audit consumer ready')
                    async with queue.iterator() as messages:
                        async for message in messages:
                            await process_event(message, collection)
            except Exception:
                logger.exception('Business event audit unavailable; retrying')
                await asyncio.sleep(2)
    finally:
        mongo.close()


if __name__ == '__main__':
    asyncio.run(run())
