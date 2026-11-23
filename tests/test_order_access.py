"""Order read permissions with signed JWTs and an in-memory database."""
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'services/order'))
os.environ['SQLALCHEMY_DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'

from app.auth_verifier import verifier
from app.dependencies import get_db
from app.routers.main_router import router
from app.sql import database, models


class OrderAccessTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public_key = cls.key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()

    async def asyncSetUp(self):
        async with database.engine.begin() as connection:
            await connection.run_sync(database.Base.metadata.drop_all)
        await database.initialize()
        async with database.SessionLocal() as db:
            for order_id, client_id in ((1, 10), (2, 20), (3, None)):
                db.add(models.Order(id=order_id, client_id=client_id, number_of_pieces=1))
                db.add(models.Piece(id=order_id, order_id=order_id, status='Queued'))
            await db.commit()

        async def session():
            async with database.SessionLocal() as db:
                yield db

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = session
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
        for mocked in (patch.object(verifier, 'key_loader', AsyncMock(return_value=self.public_key)),
                       patch.object(verifier, '_public_key', None)):
            mocked.start()
            self.addCleanup(mocked.stop)

    async def asyncTearDown(self):
        await self.client.aclose()
        await database.engine.dispose()

    def headers(self, client_id=None, admin=False, expired=False):
        now = int(time.time())
        claims = {'sub': 'access-test', 'iss': 'manufacturing-client',
                  'aud': 'manufacturing-services', 'iat': now - 60,
                  'exp': now - 1 if expired else now + 300,
                  'roles': ['admin'] if admin else ['user']}
        if client_id is not None:
            claims['client_id'] = client_id
        token = jwt.encode(claims, self.key, algorithm='RS256')
        return {'Authorization': f'Bearer {token}'}

    async def test_all_read_routes_require_a_valid_token(self):
        for headers in ({}, {'Authorization': 'Bearer invalid'}, self.headers(10, expired=True)):
            for path in ('/order', '/order/1', '/order/1/pieces'):
                with self.subTest(path=path, headers_present=bool(headers)):
                    response = await self.client.get(path, headers=headers)
                    self.assertEqual(response.status_code, 401)

    async def test_clients_only_list_their_own_orders(self):
        for client_id, expected in ((10, [1]), (20, [2]), (30, [])):
            response = await self.client.get('/order', headers=self.headers(client_id))
            self.assertEqual(response.status_code, 200)
            self.assertEqual([order['id'] for order in response.json()], expected)

    async def test_owner_can_read_order_and_pieces(self):
        for path in ('/order/1', '/order/1/pieces'):
            response = await self.client.get(path, headers=self.headers(10))
            self.assertEqual(response.status_code, 200)
            result = response.json()
            self.assertEqual(result[0]['order_id'] if isinstance(result, list) else result['id'], 1)

    async def test_other_client_cannot_read_order_or_pieces(self):
        for path in ('/order/1', '/order/1/pieces'):
            response = await self.client.get(path, headers=self.headers(20))
            self.assertEqual(response.status_code, 403)

    async def test_administrator_can_read_all_orders_and_pieces(self):
        headers = self.headers(admin=True)
        response = await self.client.get('/order', headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual({order['id'] for order in response.json()}, {1, 2, 3})
        for order_id in (1, 2, 3):
            for path in (f'/order/{order_id}', f'/order/{order_id}/pieces'):
                response = await self.client.get(path, headers=headers)
                self.assertEqual(response.status_code, 200)

    async def test_missing_client_claim_cannot_expose_unowned_orders(self):
        headers = self.headers()
        response = await self.client.get('/order', headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        for path in ('/order/3', '/order/3/pieces'):
            response = await self.client.get(path, headers=headers)
            self.assertEqual(response.status_code, 403)

    async def test_missing_order_returns_not_found(self):
        for path in ('/order/999', '/order/999/pieces'):
            response = await self.client.get(path, headers=self.headers(admin=True))
            self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
