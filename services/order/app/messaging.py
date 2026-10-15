"""Publish order.created for orders awaiting payment."""
import asyncio
import json
import logging
import os

import aio_pika
from sqlalchemy import select
from .sql import database, models

logger = logging.getLogger(__name__)
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://manufacturing:manufacturing@localhost:5672/")
EXCHANGE = os.getenv("RABBITMQ_EXCHANGE", "manufacturing.events")


async def publish_pending_events():
    while True:
        try:
            connection = await aio_pika.connect_robust(RABBITMQ_URL, timeout=5)
            async with connection:
                channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
                exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
                # Keep messages even if payment has not started yet.
                queue = await channel.declare_queue("payment.events", durable=True)
                await queue.bind(exchange, routing_key="order.created")
                while True:
                    async with database.SessionLocal() as db:
                        orders = (await db.scalars(select(models.Order).where(
                            models.Order.status == "PendingPayment",
                            models.Order.client_id.is_not(None),
                            models.Order.order_created_published.is_(False),
                            models.Order.payment_completed.is_(False),
                        ).limit(100))).all()
                        for order in orders:
                            payload = {"order_id": order.id, "client_id": order.client_id,
                                       "amount": order.total_price}
                            await exchange.publish(aio_pika.Message(
                                body=json.dumps(payload).encode(), content_type="application/json",
                                message_id=f"order.created:{order.id}", type="order.created",
                                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                            ), routing_key="order.created", mandatory=True, timeout=5)
                            order.order_created_published = True
                            await db.commit()
                    await asyncio.sleep(1)
        except Exception:
            logger.exception("RabbitMQ publication unavailable; retrying in five seconds")
            await asyncio.sleep(5)
