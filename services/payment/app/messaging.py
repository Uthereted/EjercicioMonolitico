"""Consume order.created and report the payment result through the existing REST flow."""
import asyncio
import logging
import os

import aio_pika
import httpx
from pydantic import BaseModel, Field, ValidationError
from .sql import crud, database, models

logger = logging.getLogger(__name__)
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://manufacturing:manufacturing@localhost:5672/")
EXCHANGE = os.getenv("RABBITMQ_EXCHANGE", "manufacturing.events")
ORDERS_URL = os.getenv("ORDERS_SERVICE_URL", "http://localhost:13001").rstrip("/")


class OrderCreated(BaseModel):
    order_id: int = Field(gt=0)
    client_id: int = Field(gt=0)
    amount: int = Field(gt=0)


async def handle_order_created(data):
    async with database.SessionLocal() as db:
        result = await db.get(models.OrderPaymentResult, data.order_id)
        if result is None:
            accepted, reason = True, ""
            try:
                await crud.charge(db, data.client_id, data.amount, data.order_id)
            except (crud.InsufficientFundsError, crud.PaymentConflictError) as exc:
                accepted, reason = False, str(exc)
            result = models.OrderPaymentResult(
                order_id=data.order_id, client_id=data.client_id, amount=data.amount,
                accepted=accepted, reason=reason,
            )
            db.add(result)
            await db.commit()
        if result.client_id != data.client_id or result.amount != data.amount:
            raise ValueError("Conflicting order.created payment details")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.patch(f"{ORDERS_URL}/order/{data.order_id}/payment", json={
                "client_id": result.client_id, "amount": result.amount,
                "accepted": result.accepted, "reason": result.reason,
            })
            response.raise_for_status()


async def consume_orders():
    while True:
        try:
            connection = await aio_pika.connect_robust(RABBITMQ_URL, timeout=5)
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=1)
                exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
                queue = await channel.declare_queue("payment.events", durable=True)
                await queue.bind(exchange, routing_key="order.created")
                async with queue.iterator() as messages:
                    async for message in messages:
                        try:
                            event = OrderCreated.model_validate_json(message.body)
                        except ValidationError:
                            logger.exception("Invalid order.created message; rejecting")
                            await message.reject(requeue=False)
                            continue
                        try:
                            await handle_order_created(event)
                        except ValueError:
                            logger.exception("Conflicting order.created; rejecting")
                            await message.reject(requeue=False)
                        except Exception:
                            logger.exception("Payment processing pending; retrying")
                            await asyncio.sleep(5)
                            await message.nack(requeue=True)
                        else:
                            await message.ack()
        except Exception:
            logger.exception("RabbitMQ consumer unavailable; retrying in five seconds")
            await asyncio.sleep(5)
