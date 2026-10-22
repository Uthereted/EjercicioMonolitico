"""Consume manufacturing requests and publish piece status events via RabbitMQ."""
import asyncio
import json
import logging
import os
from contextlib import suppress
from typing import List, Literal

import aio_pika
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://manufacturing:manufacturing@localhost:5672/")
EXCHANGE = os.getenv("RABBITMQ_EXCHANGE", "manufacturing.events")


class PiecesRequested(BaseModel):
    order_id: int = Field(gt=0)
    piece_ids: List[int] = Field(min_length=1)


class Publisher:
    """Keeps one confirmed channel open to publish piece.status.update events."""

    def __init__(self):
        self.connection = None
        self.exchange = None
        self.lock = asyncio.Lock()

    async def _ensure_channel(self):
        if self.exchange is None:
            self.connection = await aio_pika.connect_robust(RABBITMQ_URL, timeout=5)
            channel = await self.connection.channel(publisher_confirms=True, on_return_raises=True)
            self.exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
            # Keep messages even if order has not started yet.
            queue = await channel.declare_queue("order.events", durable=True)
            await queue.bind(self.exchange, routing_key="piece.status.update")

    async def publish_piece_status(self, piece_id: int, status: Literal["Manufacturing", "Manufactured"]):
        async with self.lock:
            try:
                await self._ensure_channel()
                await self.exchange.publish(aio_pika.Message(
                    body=json.dumps({"piece_id": piece_id, "status": status}).encode(),
                    content_type="application/json",
                    message_id=f"piece.status.update:{piece_id}:{status}",
                    type="piece.status.update",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ), routing_key="piece.status.update", mandatory=True, timeout=5)
            except Exception:
                await self.close()
                raise

    async def close(self):
        if self.connection is not None:
            with suppress(Exception):
                await self.connection.close()
        self.connection = None
        self.exchange = None


async def consume_manufacturing_requests(machine):
    while True:
        try:
            connection = await aio_pika.connect_robust(RABBITMQ_URL, timeout=5)
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=10)
                exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
                queue = await channel.declare_queue("machine.events", durable=True)
                await queue.bind(exchange, routing_key="machine.pieces.requested")
                async with queue.iterator() as messages:
                    async for message in messages:
                        try:
                            event = PiecesRequested.model_validate_json(message.body)
                        except ValidationError:
                            logger.exception("Invalid machine.pieces.requested; rejecting")
                            await message.reject(requeue=False)
                            continue
                        await machine.add_pieces_to_queue(event.piece_ids)
                        await message.ack()
        except Exception:
            logger.exception("Machine consumer unavailable; retrying in five seconds")
            await asyncio.sleep(5)