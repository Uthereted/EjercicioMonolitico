import asyncio
import logging
import os

import aio_pika
from pydantic import ValidationError

from pydantic import BaseModel, Field
from typing import Literal

from . import workflow
from .sql import crud, schemas, database

logger = logging.getLogger(__name__)


class OrderDeliveryReady(BaseModel):
    order_id: int = Field(gt=0)
    delivery_id: int = Field(gt=0)
    status: Literal["Ready"]


async def handle_order_delivery_ready(db, event: OrderDeliveryReady):
    delivery = await crud.get_delivery(db, event.delivery_id)

    if delivery is None:
        raise ValueError(f"Delivery {event.delivery_id} not found")

    if delivery.order_id != event.order_id:
        raise ValueError("Delivery does not belong to the order")

    update = schemas.DeliveryStatusUpdate(status=event.status)
    return await workflow.set_delivery_status(db, event.delivery_id, update)

RABBITMQ_URL = os.getenv(
    "RABBITMQ_URL",
    "amqp://manufacturing:manufacturing@localhost:5672/",
)
EXCHANGE = os.getenv("RABBITMQ_EXCHANGE", "manufacturing.events")


async def consume_delivery_events():
    while True:
        try:
            connection = await aio_pika.connect_robust(
                RABBITMQ_URL,
                timeout=5,
            )

            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=1)

                exchange = await channel.declare_exchange(
                    EXCHANGE,
                    aio_pika.ExchangeType.TOPIC,
                    durable=True,
                )

                queue = await channel.declare_queue(
                    "delivery.events",
                    durable=True,
                )

                await queue.bind(
                    exchange,
                    routing_key="order.delivery.ready",
                )

                async with queue.iterator() as messages:
                    async for message in messages:
                        async with database.SessionLocal() as db:
                            try:
                                event = OrderDeliveryReady.model_validate_json(
                                    message.body
                                )
                                await handle_order_delivery_ready(db, event)
                            except (ValidationError, ValueError):
                                logger.exception(
                                    "Invalid delivery event; rejecting"
                                )
                                await message.reject(requeue=False)
                            except Exception:
                                logger.exception(
                                    "Delivery event processing failed; requeueing"
                                )
                                await message.nack(requeue=True)
                            else:
                                await message.ack()

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Delivery RabbitMQ consumer unavailable; retrying"
            )
            await asyncio.sleep(5)