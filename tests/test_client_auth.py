"""Client authentication/authorization and user token issuance against an ASGI app."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/client'))
os.environ['SQLALCHEMY_DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
from app.main import app
from app import crypto
from app.dependencies import get_db
from app.sql import database, models, crud


class ClientAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with database.engine.begin() as connection:
            await connection.run_sync(models.Base.metadata.drop_all)
            await connection.run_sync(models.Base.metadata.create_all)
        async with database.SessionLocal() as db:
            await crud.seed_initial_data(db)

        async def session():
            async with database.SessionLocal() as db:
                yield db
        app.dependency_overrides[get_db] = session
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.clear()
        await database.engine.dispose()

    async def login(self, username='admin', password='admin123', method='POST'):
        response = await self.client.request(method, '/auth/token', auth=(username, password))
        self.assertEqual(response.status_code, 200, response.text)
        return {'Authorization': f"Bearer {response.json()['access_token']}"}

    async def register(self):
        response = await self.client.post('/auth/register', json={
            'username': 'alice', 'email': 'alice@example.org', 'password': 'alice-password',
        })
        self.assertEqual(response.status_code, 201, response.text)
        return await self.login('alice', 'alice-password')

    async def test_client_routes_require_bearer_authentication(self):
        routes = [('GET', '/clients', None), ('GET', '/clients/1', None),
                  ('POST', '/clients', {'name': 'Alice', 'email': 'alice@example.org'})]
        for method, path, body in routes:
            for headers in ({}, {'Authorization': 'Bearer invalid'}):
                response = await self.client.request(method, path, headers=headers, json=body)
                self.assertEqual(response.status_code, 401, response.text)
                self.assertEqual(response.headers['www-authenticate'], 'Bearer')

    async def test_request_token_requires_basic_credentials_for_get_and_post(self):
        for method in ('GET', 'POST'):
            for credentials in (None, ('admin', 'wrong'), ('unknown', 'wrong')):
                response = await self.client.request(method, '/auth/token', auth=credentials)
                self.assertEqual(response.status_code, 401, response.text)
                self.assertEqual(response.headers['www-authenticate'], 'Basic')
            headers = await self.login(method=method)
            payload = jwt.decode(headers['Authorization'][7:], crypto.get_public_key_pem(),
                                 algorithms=['RS256'], issuer='manufacturing-client',
                                 audience='manufacturing-services')
            self.assertIn('admin', payload['roles'])
            self.assertLess(payload['iat'], payload['exp'])
        response = await self.client.post('/auth/token', headers=headers)
        self.assertEqual(response.status_code, 401)

    async def test_user_can_create_and_read_only_own_client(self):
        headers = await self.register()
        response = await self.client.post('/clients', headers=headers,
                                          json={'name': 'Alice', 'email': 'alice@example.org'})
        self.assertEqual(response.status_code, 201, response.text)
        own_id = response.json()['id']
        # The token predates client creation, but signed email still identifies its owner.
        response = await self.client.get(f'/clients/{own_id}', headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.get('/clients', headers=headers)
        self.assertEqual([row['id'] for row in response.json()], [own_id])
        response = await self.client.post('/clients', headers=headers,
                                          json={'name': 'Bob', 'email': 'bob@example.org'})
        self.assertEqual(response.status_code, 403)
        response = await self.client.post('/clients', headers=headers,
                                          json={'name': 'Alice duplicate', 'email': 'alice@example.org'})
        self.assertEqual(response.status_code, 409)
        admin = await self.login()
        other = await self.client.post('/clients', headers=admin,
                                       json={'name': 'Bob', 'email': 'bob@example.org'})
        other_id = other.json()['id']
        response = await self.client.get(f'/clients/{other_id}', headers=headers)
        self.assertEqual(response.status_code, 403)
        response = await self.client.get('/clients', headers=admin)
        self.assertEqual(len(response.json()), 2)
        response = await self.client.get(f'/clients/{other_id}', headers=admin)
        self.assertEqual(response.status_code, 200)
        refreshed = await self.login('alice', 'alice-password')
        payload = jwt.decode(refreshed['Authorization'][7:], crypto.get_public_key_pem(),
                             algorithms=['RS256'], audience='manufacturing-services')
        self.assertEqual(payload['client_id'], own_id)

    async def test_service_identities_can_read_but_not_create_or_list_clients(self):
        admin = await self.login()
        response = await self.client.post('/clients', headers=admin,
                                          json={'name': 'Alice', 'email': 'alice@example.org'})
        client_id = response.json()['id']
        with patch.dict(os.environ, {'ORDER_SERVICE_AUTH_SECRET': 'order-secret',
                                     'PAYMENT_SERVICE_AUTH_SECRET': 'payment-secret'}):
            for name, secret in (('order-service', 'order-secret'), ('payment-service', 'payment-secret')):
                token = await self.client.post('/auth/service-token', auth=(name, secret))
                self.assertEqual(token.status_code, 200, token.text)
                headers = {'Authorization': f"Bearer {token.json()['access_token']}"}
                response = await self.client.get(f'/clients/{client_id}', headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                response = await self.client.get('/clients/999', headers=headers)
                self.assertEqual(response.status_code, 404)
                response = await self.client.get('/clients', headers=headers)
                self.assertEqual(response.status_code, 403)
                response = await self.client.post('/clients', headers=headers,
                                                  json={'name': 'Bob', 'email': 'bob@example.org'})
                self.assertEqual(response.status_code, 403)
            response = await self.client.post('/auth/service-token', auth=('payment-service', 'order-secret'))
            self.assertEqual(response.status_code, 401)

    async def test_invalid_token_claims_are_rejected(self):
        now = datetime.now(timezone.utc)
        payload = {'sub': '1', 'iat': now, 'exp': now + timedelta(minutes=5),
                   'iss': 'manufacturing-client', 'aud': 'manufacturing-services'}
        crypto.generate_rsa_keys()
        variants = [{**payload, 'iss': 'wrong'}, {**payload, 'aud': 'wrong'},
                    {**payload, 'exp': now - timedelta(seconds=1)}]
        variants.extend({k: v for k, v in payload.items() if k != claim} for claim in ('exp', 'iat', 'sub'))
        for invalid in variants:
            token = jwt.encode(invalid, crypto._private_key, algorithm='RS256')
            response = await self.client.get('/clients', headers={'Authorization': f'Bearer {token}'})
            self.assertEqual(response.status_code, 401, response.text)

    async def test_public_endpoints_and_empty_user_client_list(self):
        response = await self.client.get('/auth/public-key')
        self.assertEqual(response.status_code, 200)
        headers = await self.register()
        response = await self.client.get('/clients', headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])


if __name__ == '__main__':
    unittest.main()
