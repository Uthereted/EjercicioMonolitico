"""Publish order.created and consume payment and delivery results."""
import asyncio
import json
import logging
import os
from typing import Literal

import aio_pika
from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from .sql import database, models

logger = logging.getLogger(__name__)
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://manufacturing:manufacturing@localhost:5672/")
EXCHANGE = os.getenv("RABBITMQ_EXCHANGE", "manufacturing.events")


class DeliveryStatusUpdate(BaseModel):
    delivery_id: int = Field(gt=0)
    order_id: int = Field(gt=0)
    status: Literal["Delivered"]


class PaymentResultEvent(BaseModel):
    order_id: int = Field(gt=0)
    client_id: int = Field(gt=0)
    amount: int = Field(gt=0)
    accepted: bool
    reason: str = "Payment rejected"


async def handle_delivery_update(event):
    from .routers import main_router
    from .sql import schemas

    async with database.SessionLocal() as db:
        order = await main_router.require_order(db, event.order_id)
        if order.delivery_id != event.delivery_id:
            raise ValueError("Delivery does not match the order")
        await main_router.update_order_status(
            event.order_id, schemas.OrderStatusUpdate(status=event.status), db,
        )


async def consume_delivery_updates():
    while True:
        try:
            connection = await aio_pika.connect_robust(RABBITMQ_URL, timeout=5)
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=1)
                exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
                queue = await channel.declare_queue("order.events", durable=True)
                await queue.bind(exchange, routing_key="delivery.status.update")
                async with queue.iterator() as messages:
                    async for message in messages:
                        try:
                            event = DeliveryStatusUpdate.model_validate_json(message.body)
                            await handle_delivery_update(event)
                        except (ValidationError, ValueError):
                            logger.exception("Invalid delivery.status.update; rejecting")
                            await message.reject(requeue=False)
                        except Exception:
                            logger.exception("Delivery update pending; retrying")
                            await asyncio.sleep(5)
                            await message.nack(requeue=True)
                        else:
                            await message.ack()
        except Exception:
            logger.exception("Delivery consumer unavailable; retrying in five seconds")
            await asyncio.sleep(5)


async def handle_payment_result(event: PaymentResultEvent):
    """Apply a payment.approved/payment.rejected event idempotently."""
    from .routers import main_router
    from .sql import schemas

    async with database.SessionLocal() as db:
        try:
            await main_router.payment_result(
                event.order_id, schemas.PaymentResult(**event.model_dump(exclude={"order_id"})), db,
            )
        except HTTPException as exc:
            if exc.status_code in (404, 409):
                raise ValueError(exc.detail) from exc
            raise


async def consume_payment_results():
    """Consume payment.approved and payment.rejected events from RabbitMQ."""
    while True:
        try:
            connection = await aio_pika.connect_robust(RABBITMQ_URL, timeout=5)
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=1)
                exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
                queue = await channel.declare_queue("order.payment.events", durable=True)
                await queue.bind(exchange, routing_key="payment.approved")
                await queue.bind(exchange, routing_key="payment.rejected")
                async with queue.iterator() as messages:
                    async for message in messages:
                        try:
                            event = PaymentResultEvent.model_validate_json(message.body)
                            expected = "payment.approved" if event.accepted else "payment.rejected"
                            if message.routing_key != expected:
                                raise ValueError("Payment event routing key does not match its result")
                            await handle_payment_result(event)
                        except (ValidationError, ValueError):
                            logger.exception("Invalid/conflicting payment result; rejecting")
                            await message.reject(requeue=False)
                        except Exception:
                            logger.exception("Payment result processing pending; retrying")
                            await asyncio.sleep(5)
                            await message.nack(requeue=True)
                        else:
                            await message.ack()
        except Exception:
            logger.exception("Payment result consumer unavailable; retrying in five seconds")
            await asyncio.sleep(5)


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
