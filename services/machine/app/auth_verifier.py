import asyncio
import logging
import os
import aio_pika
import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

CLIENT_SERVICE_URL = os.getenv("CLIENT_SERVICE_URL", "http://client_service:8000").rstrip("/")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://manufacturing:manufacturing@rabbitmq:5672/")
EXCHANGE_NAME = os.getenv("RABBITMQ_EXCHANGE", "manufacturing.events")

_public_key = None
security_bearer = HTTPBearer()


async def fetch_public_key():
    global _public_key
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{CLIENT_SERVICE_URL}/auth/public-key")
            resp.raise_for_status()
            _public_key = resp.json()["public_key"]
            logger.info("Clave pública RSA descargada correctamente en Delivery Service")
    except Exception as e:
        logger.warning(f"No se pudo descargar la clave pública: {e}")


async def listen_for_cert_updates():
    while True:
        try:
            connection = await aio_pika.connect_robust(RABBITMQ_URL, timeout=5)
            async with connection:
                channel = await connection.channel()
                exchange = await channel.declare_exchange(
                    EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
                )
                queue = await channel.declare_queue("", exclusive=True)
                await queue.bind(exchange, routing_key="auth.cert_updated")

                async with queue.iterator() as messages:
                    async for message in messages:
                        async with message.process():
                            await fetch_public_key()
        except Exception:
            await asyncio.sleep(5)


async def verify_jwt(credentials: HTTPAuthorizationCredentials = Depends(security_bearer)) -> dict:
    if not _public_key:
        await fetch_public_key()
        if not _public_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Clave pública de autenticación no disponible"
            )

    token = credentials.credentials
    try:
        return jwt.decode(token, _public_key, algorithms=["RS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="El token ha expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token JWT inválido")