"""Consume order.created and publish payment.approved or payment.rejected."""
import asyncio
import logging
import os

import aio_pika
from my_chassis.messaging import publish_json_event
from pydantic import BaseModel, Field, ValidationError
from .sql import crud, database, models

logger = logging.getLogger(__name__)
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://manufacturing:manufacturing@localhost:5672/")
EXCHANGE = os.getenv("RABBITMQ_EXCHANGE", "manufacturing.events")


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
        await publish_payment_result(result)


async def publish_payment_result(result):
    """Publish a durable payment outcome for Order Service to consume."""
    routing_key = "payment.approved" if result.accepted else "payment.rejected"
    payload = {
        "order_id": result.order_id,
        "client_id": result.client_id,
        "amount": result.amount,
        "accepted": result.accepted,
        "reason": result.reason,
    }
    await publish_json_event(
        RABBITMQ_URL, EXCHANGE, routing_key, "order.payment.events", payload,
        f"{routing_key}:{result.order_id}", bindings=("payment.approved", "payment.rejected"),
    )
    logger.info("Published %s for order %s", routing_key, result.order_id)


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
