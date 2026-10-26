"""Verify locally issued access tokens without calling the auth service itself."""
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import crypto

security = HTTPBearer(auto_error=False)


async def verify_jwt(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    try:
        return jwt.decode(
            credentials.credentials, crypto.get_public_key_pem(), algorithms=["RS256"],
            issuer="manufacturing-client", audience="manufacturing-services",
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})


def is_admin(payload: dict) -> bool:
    return "admin" in payload.get("roles", [])


def is_client_reader(payload: dict) -> bool:
    """Only explicitly issued service identities can read clients for validation."""
    name = payload.get("service_name")
    return name in ("order-service", "payment-service") and payload.get("sub") == f"service:{name}"
