"""Run in order and payment environments with their app on PYTHONPATH."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from app import auth_verifier


class AuthVerifierTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.public_key = self.key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        self.key_patch = patch.object(auth_verifier, "_public_key", self.public_key)
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)
        now = datetime.now(timezone.utc)
        self.payload = {"sub": "1", "client_id": 3, "roles": ["user"], "iat": now,
                        "exp": now + timedelta(minutes=5), "iss": "manufacturing-client",
                        "aud": "manufacturing-services"}

    def credentials(self, payload=None, key=None):
        token = jwt.encode(payload or self.payload, key or self.key, algorithm="RS256")
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    async def test_valid_rs256_token(self):
        payload = await auth_verifier.verify_jwt(self.credentials())
        self.assertEqual(payload["client_id"], 3)

    async def test_missing_credentials(self):
        with self.assertRaises(HTTPException) as error:
            await auth_verifier.verify_jwt(None)
        self.assertEqual(error.exception.status_code, 401)

    async def test_expired_wrong_audience_and_missing_expiry(self):
        variants = [{**self.payload, "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
                    {**self.payload, "aud": "another-service"},
                    {k: v for k, v in self.payload.items() if k != "exp"}]
        for payload in variants:
            with self.assertRaises(HTTPException) as error:
                await auth_verifier.verify_jwt(self.credentials(payload))
            self.assertEqual(error.exception.status_code, 401)

    async def test_invalid_signature_is_rejected_after_refresh(self):
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with patch.object(auth_verifier, "fetch_public_key", new=AsyncMock(return_value=True)):
            with self.assertRaises(HTTPException) as error:
                await auth_verifier.verify_jwt(self.credentials(key=other_key))
        self.assertEqual(error.exception.status_code, 401)

    async def test_rotation_refreshes_cached_key(self):
        auth_verifier._public_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()

        async def refresh():
            auth_verifier._public_key = self.public_key
            return True

        with patch.object(auth_verifier, "fetch_public_key", new=AsyncMock(side_effect=refresh)) as fetch:
            payload = await auth_verifier.verify_jwt(self.credentials())
            fetch.assert_awaited_once()
        self.assertEqual(payload["sub"], "1")


if __name__ == "__main__":
    unittest.main()
