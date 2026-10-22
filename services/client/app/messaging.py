"""Announce signing key changes without publishing private key material."""
import asyncio
import logging
import os

import aio_pika

logger = logging.getLogger(__name__)


async def notify_cert_updated():
    while True:
        try:
            connection = await aio_pika.connect_robust(os.getenv(
                "RABBITMQ_URL", "amqp://manufacturing:manufacturing@localhost:5672/"), timeout=5)
            async with connection:
                channel = await connection.channel(publisher_confirms=True)
                exchange = await channel.declare_exchange(
                    os.getenv("RABBITMQ_EXCHANGE", "manufacturing.events"),
                    aio_pika.ExchangeType.TOPIC, durable=True,
                )
                await exchange.publish(aio_pika.Message(body=b'{}', content_type="application/json",
                                                       type="client.cert.updated"),
                                       routing_key="client.cert.updated", mandatory=False, timeout=5)
            return
        except Exception:
            logger.exception("Signing key announcement pending; retrying")
            await asyncio.sleep(5)
