"""Transactional outbox/inbox and topic subscriptions for domain facts."""
import asyncio
import json
import logging
import os

import aio_pika
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from pydantic import ValidationError
from sqlalchemy import Boolean, Column, Integer, String, Text, select, update
from sqlalchemy.dialects.sqlite import insert

from .sql import database

logger = logging.getLogger(__name__)
SUBSCRIPTIONS = {
    "order.domain": ("client.created", "clients.catalogue.published", "payment.approved",
                     "payment.rejected", "piece.manufacturing.started", "piece.manufactured",
                     "delivery.created", "delivery.ready", "delivery.delivered", "auth.signing-key.published"),
    "payment.domain": ("client.created", "clients.catalogue.published", "order.created", "auth.signing-key.published"),
    "delivery.domain": ("order.paid", "order.manufactured", "auth.signing-key.published"),
    "machine.domain": ("order.paid", "auth.signing-key.published"),
}


class Outbox(database.Base):
    __tablename__ = "event_outbox"
    id = Column(Integer, primary_key=True)
    event_id = Column(String(200), unique=True, nullable=False)
    event_type = Column(String(100), nullable=False)
    payload = Column(Text, nullable=False)
    published = Column(Boolean, nullable=False, default=False)


class Inbox(database.Base):
    __tablename__ = "event_inbox"
    event_id = Column(String(200), primary_key=True)


class SigningKey(database.Base):
    __tablename__ = "event_signing_key"
    id = Column(Integer, primary_key=True)
    pem = Column(Text, nullable=False)


class RetryEvent(Exception):
    """A causal prerequisite has not arrived yet; redelivery can succeed."""


async def record_event(db, event_type, payload, event_id):
    await db.execute(insert(Outbox).values(
        event_id=event_id, event_type=event_type, payload=json.dumps(payload), published=False,
    ).on_conflict_do_nothing(index_elements=["event_id"]))


async def provision(channel):
    exchange = await channel.declare_exchange(os.getenv("RABBITMQ_EXCHANGE", "manufacturing.events"),
                                              aio_pika.ExchangeType.TOPIC, durable=True)
    # Infrastructure topology preserves events even while subscribers are offline.
    # Business publishers address a fact type, never an individual service queue.
    for queue_name, topics in SUBSCRIPTIONS.items():
        queue = await channel.declare_queue(queue_name, durable=True)
        for topic in topics:
            await queue.bind(exchange, routing_key=topic)
    return exchange


async def flush_outbox(exchange):
    async with database.SessionLocal() as db:
        pending = list((await db.execute(select(Outbox.id, Outbox.event_id, Outbox.event_type, Outbox.payload).where(
            Outbox.published.is_(False)).order_by(Outbox.id).limit(100))).all())
        for event in pending:
            await exchange.publish(aio_pika.Message(
                body=json.dumps({"event_id": event.event_id, "event_type": event.event_type,
                                 "data": json.loads(event.payload)}).encode(),
                content_type="application/json", type=event.event_type, message_id=event.event_id,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ), routing_key=event.event_type, mandatory=True, timeout=5)
            await db.execute(update(Outbox).where(Outbox.id == event.id).values(published=True))
            await db.commit()


async def publish_pending_events():
    while True:
        try:
            connection = await aio_pika.connect_robust(os.getenv(
                "RABBITMQ_URL", "amqp://manufacturing:manufacturing@localhost:5672/"), timeout=5)
            async with connection:
                channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
                exchange = await provision(channel)
                while True:
                    await flush_outbox(exchange)
                    await asyncio.sleep(0.25)
        except Exception:
            logger.exception("Event outbox publication pending; retrying")
            await asyncio.sleep(2)


async def dispatch(event_id, event_type, data, handler):
    async with database.SessionLocal() as db:
        # SQLite acquires the write transaction before effects are applied.
        claimed = await db.execute(insert(Inbox).values(event_id=event_id).on_conflict_do_nothing())
        if claimed.rowcount == 0:
            await db.rollback()
            return
        if event_type == "auth.signing-key.published":
            pem = data["public_key"]
            if not isinstance(pem, str) or not isinstance(load_pem_public_key(pem.encode()), RSAPublicKey):
                raise ValueError("Invalid RSA public key")
            await db.execute(insert(SigningKey).values(id=1, pem=pem).on_conflict_do_update(
                index_elements=["id"], set_={"pem": pem}))
        else:
            await handler(db, event_type, data)
        await db.commit()


async def process_event(message, handler):
    try:
        envelope = json.loads(message.body)
        if (not isinstance(envelope["event_id"], str) or not envelope["event_id"]
                or envelope["event_type"] != message.routing_key or not isinstance(envelope["data"], dict)):
            raise ValueError("Invalid event envelope")
        await dispatch(envelope["event_id"], envelope["event_type"], envelope["data"], handler)
    except (ValidationError, ValueError, KeyError, TypeError):
        logger.exception("Invalid domain event; rejecting")
        await message.reject(requeue=False)
    except Exception:
        logger.exception("Domain event pending; retrying")
        await asyncio.sleep(1)
        await message.nack(requeue=True)
    else:
        await message.ack()


async def consume_events(queue_name, handler):
    while True:
        try:
            connection = await aio_pika.connect_robust(os.getenv(
                "RABBITMQ_URL", "amqp://manufacturing:manufacturing@localhost:5672/"), timeout=5)
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=1)
                await provision(channel)
                queue = await channel.declare_queue(queue_name, durable=True)
                async with queue.iterator() as messages:
                    async for message in messages:
                        await process_event(message, handler)
        except Exception:
            logger.exception("Domain event consumer unavailable; retrying")
            await asyncio.sleep(2)
