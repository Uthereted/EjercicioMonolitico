"""Verify RS256 access tokens; refresh the public key after client restarts."""
import asyncio
import logging
import os

import aio_pika
import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)
_public_key = None
CLIENT_URL = os.getenv("CLIENT_SERVICE_URL", "http://localhost:13005").rstrip("/")


async def fetch_public_key():
    global _public_key
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{CLIENT_URL}/auth/public-key")
            response.raise_for_status()
            key = response.json()["public_key"]
            if not isinstance(key, str):
                raise ValueError('Expected a PEM public key string')
            if not isinstance(load_pem_public_key(key.encode()), RSAPublicKey):
                raise ValueError('Expected an RSA public key')
            _public_key = key
        return True
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        logger.warning("Auth service unavailable; public key refresh will retry")
        return False


async def verify_jwt(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials is None:
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    if _public_key is None and not await fetch_public_key():
        raise HTTPException(503, "Auth service is unavailable")
    for attempt in range(2):
        try:
            return jwt.decode(credentials.credentials, _public_key, algorithms=["RS256"],
                              issuer="manufacturing-client", audience="manufacturing-services",
                              options={"require": ["exp", "iat", "sub"]})
        except jwt.InvalidSignatureError:
            if attempt == 0 and await fetch_public_key():
                continue
            break
        except jwt.InvalidTokenError:
            break
    raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})


async def listen_for_cert_updates():
    """Each service instance gets its own queue, separate from order.events."""
    while True:
        try:
            connection = await aio_pika.connect_robust(os.getenv(
                "RABBITMQ_URL", "amqp://manufacturing:manufacturing@localhost:5672/"), timeout=5)
            async with connection:
                channel = await connection.channel()
                exchange = await channel.declare_exchange(
                    os.getenv("RABBITMQ_EXCHANGE", "manufacturing.events"),
                    aio_pika.ExchangeType.TOPIC, durable=True,
                )
                queue = await channel.declare_queue(exclusive=True, auto_delete=True)
                await queue.bind(exchange, routing_key="client.cert.updated")
                await fetch_public_key()
                async with queue.iterator() as messages:
                    async for message in messages:
                        if await fetch_public_key():
                            await message.ack()
                        else:
                            await asyncio.sleep(5)
                            await message.nack(requeue=True)
        except Exception:
            logger.exception("Certificate consumer unavailable; retrying")
            await asyncio.sleep(5)
