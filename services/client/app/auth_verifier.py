"""Local signing-key provider and service-specific authorization policies."""
from my_chassis.auth import JWTVerifier
from . import crypto


async def local_key():
    return crypto.get_public_key_pem()


verifier = JWTVerifier(key_loader=local_key, issuer="manufacturing-client", audience="manufacturing-services")
verify_jwt = verifier.verify_jwt


def is_admin(payload: dict) -> bool:
    return "admin" in payload.get("roles", [])


def is_client_reader(payload: dict) -> bool:
    """Only explicitly issued service identities can read clients for validation."""
    name = payload.get("service_name")
    return name in ("order-service", "payment-service") and payload.get("sub") == f"service:{name}"
