"""Password hashes and the authentication service's RSA signing key."""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_private_key = None


def generate_rsa_keys():
    global _private_key
    if _private_key is None:
        _private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def get_public_key_pem():
    generate_rsa_keys()
    return _private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password, password_hash):
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_jwt_token(payload):
    generate_rsa_keys()
    now = datetime.now(timezone.utc)
    return jwt.encode({**payload, "iat": now, "exp": now + timedelta(hours=1),
                       "iss": "manufacturing-client", "aud": "manufacturing-services"},
                      _private_key, algorithm="RS256")
