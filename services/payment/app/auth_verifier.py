"""Verify RS256 access tokens; refresh the public key after client restarts."""
import logging

import aio_pika
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from .events import SigningKey
from .sql import database
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)
_public_key = None


async def fetch_public_key():
    global _public_key
    async with database.SessionLocal() as db:
        cached = await db.get(SigningKey, 1)
        if cached is None:
            return False
        key = cached.pem
        if not isinstance(load_pem_public_key(key.encode()), RSAPublicKey):
            return False
        _public_key = key
        return True


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
