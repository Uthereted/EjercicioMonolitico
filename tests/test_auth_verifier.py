"""Run in each service environment with its app on PYTHONPATH."""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import httpx
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
                    {**self.payload, "iss": "another-issuer"}]
        variants.extend({k: v for k, v in self.payload.items() if k != claim}
                        for claim in ("exp", "iat", "sub"))
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

    async def test_malformed_and_wrong_algorithm_tokens_are_rejected(self):
        for token in ("invalid", jwt.encode(self.payload, "test-secret" * 4, algorithm="HS256")):
            credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
            with self.assertRaises(HTTPException) as error:
                await auth_verifier.verify_jwt(credentials)
            self.assertEqual(error.exception.status_code, 401)

    async def test_missing_key_and_unavailable_auth_returns_503(self):
        auth_verifier._public_key = None
        with patch.object(auth_verifier, "fetch_public_key", new=AsyncMock(return_value=False)):
            with self.assertRaises(HTTPException) as error:
                await auth_verifier.verify_jwt(self.credentials())
        self.assertEqual(error.exception.status_code, 503)

    async def test_public_key_fetch_validates_and_preserves_cached_key(self):
        request = httpx.Request("GET", "http://auth/auth/public-key")
        with patch.object(auth_verifier.httpx, "AsyncClient") as client:
            get = client.return_value.__aenter__.return_value.get
            get.return_value = httpx.Response(200, json={"public_key": self.public_key}, request=request)
            self.assertTrue(await auth_verifier.fetch_public_key())
            get.return_value = httpx.Response(200, json={"public_key": "invalid PEM"}, request=request)
            self.assertFalse(await auth_verifier.fetch_public_key())
            self.assertEqual(auth_verifier._public_key, self.public_key)

    async def test_certificate_listener_uses_published_event_and_retries_refresh(self):
        with patch.object(auth_verifier.aio_pika, "connect_robust", new=AsyncMock()) as connect:
            channel = connect.return_value.channel.return_value
            queue = channel.declare_queue.return_value
            queue.iterator = MagicMock()
            queue.iterator.return_value.__aenter__ = AsyncMock()
            message = AsyncMock()
            queue.iterator.return_value.__aenter__.return_value.__aiter__.return_value = [message]
            # Stop the reconnect loop after the first connection has closed.
            connect.side_effect = [connect.return_value, asyncio.CancelledError()]
            with patch.object(auth_verifier, "fetch_public_key", new=AsyncMock(side_effect=[True, False])):
                with patch.object(auth_verifier.asyncio, "sleep", new=AsyncMock()):
                    with self.assertRaises(asyncio.CancelledError):
                        await auth_verifier.listen_for_cert_updates()
            queue.bind.assert_awaited_once_with(
                channel.declare_exchange.return_value, routing_key="client.cert.updated")
            message.nack.assert_awaited_once_with(requeue=True)
            message.ack.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
