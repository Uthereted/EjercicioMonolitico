"""Run separately with AUTH_TEST_SERVICE=machine or delivery."""
import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

SERVICE = os.getenv("AUTH_TEST_SERVICE", "delivery")
if SERVICE not in ("machine", "delivery"):
    raise ValueError("AUTH_TEST_SERVICE must be machine or delivery")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / SERVICE))
os.environ["SQLALCHEMY_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from app import auth_verifier
if SERVICE == "machine":
    import main as service_module
    from main import app
    from app.dependencies import get_machine
else:
    from app import main as service_module
    from app.main import app
    from app.dependencies import get_db
    from app.sql import crud


class ProtectedRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.key_patch = patch.object(auth_verifier, "_public_key", self.key.public_key())
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)
        self.refresh = patch.object(auth_verifier, "fetch_public_key", new=AsyncMock(return_value=False))
        self.refresh.start()
        self.addCleanup(self.refresh.stop)
        if SERVICE == "machine":
            self.machine = AsyncMock()
            self.machine.status = "Idle"
            self.machine.working_piece = None
            self.machine.list_queued_pieces.return_value = []

            async def machine_dependency():
                return self.machine
            app.dependency_overrides[get_machine] = machine_dependency
        else:
            async def database_dependency():
                yield object()
            app.dependency_overrides[get_db] = database_dependency
        self.addCleanup(app.dependency_overrides.clear)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()

    def token(self):
        now = datetime.now(timezone.utc)
        return jwt.encode({"sub": "1", "iat": now, "exp": now + timedelta(minutes=5),
                           "iss": "manufacturing-client", "aud": "manufacturing-services"},
                          self.key, algorithm="RS256")

    async def test_all_business_routes_reject_missing_and_invalid_tokens(self):
        routes = ([('GET', '/machine/status', None), ('POST', '/machine/queue', [1]),
                   ('DELETE', '/machine/queue/1', None)] if SERVICE == "machine" else [
                       ('POST', '/deliveries/delivery', {"order_id": 1, "client_id": 1, "address": "Madrid"}),
                       ('GET', '/deliveries/delivery', None), ('GET', '/deliveries/delivery/1', None),
                       ('DELETE', '/deliveries/delivery/1', None),
                       ('PATCH', '/deliveries/delivery/1/status', {"status": "Delivered"}),
                   ])
        for method, path, body in routes:
            for headers in ({}, {"Authorization": "Bearer invalid"}):
                with self.subTest(method=method, path=path, headers=headers):
                    response = await self.client.request(method, path, json=body, headers=headers)
                    self.assertEqual(response.status_code, 401, response.text)
                    self.assertEqual(response.headers['www-authenticate'], 'Bearer')

    async def test_current_client_token_is_accepted(self):
        headers = {"Authorization": f"Bearer {self.token()}"}
        if SERVICE == "machine":
            response = await self.client.get('/machine/status', headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['queue'], [])
        else:
            with patch.object(crud, 'get_delivery_list', new=AsyncMock(return_value=[])):
                response = await self.client.get('/deliveries/delivery', headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), [])

    async def test_shutdown_cancels_event_subscriber(self):
        started, stopped = asyncio.Event(), asyncio.Event()

        async def subscriber(*args):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        with ExitStack() as patches:
            patches.enter_context(patch.object(service_module, "fetch_public_key", new=AsyncMock()))
            engine = patches.enter_context(patch.object(service_module.database, "engine"))
            engine.begin.return_value.__aenter__.return_value.run_sync = AsyncMock()
            engine.dispose = AsyncMock()
            patches.enter_context(patch.object(service_module.events, "consume_events", new=subscriber))
            patches.enter_context(patch.object(service_module.events, "publish_pending_events", new=AsyncMock()))
            if SERVICE == "machine":
                patches.enter_context(patch.object(service_module, "get_machine", new=AsyncMock()))
                patches.enter_context(patch.object(service_module, "stop_machine", new=AsyncMock()))
            async with service_module.lifespan(app):
                await asyncio.wait_for(started.wait(), timeout=1)
            self.assertTrue(stopped.is_set())


if __name__ == '__main__':
    unittest.main()
