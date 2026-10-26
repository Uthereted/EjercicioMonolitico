"""Service identity issuance must never accept user credentials or no secret."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/client'))
os.environ['SQLALCHEMY_DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
from app.main import app
from app import crypto


class ServiceTokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_credentials_are_required_and_token_uses_client_signing_key(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.dict(os.environ, {'ORDER_SERVICE_AUTH_SECRET': 'test-only-secret'}):
                for credentials in (None, ('order-service', 'wrong'), ('admin', 'test-only-secret')):
                    response = await client.post('/auth/service-token', auth=credentials)
                    self.assertEqual(response.status_code, 401, response.text)
                response = await client.post('/auth/service-token', auth=('order-service', 'test-only-secret'))
                self.assertEqual(response.status_code, 200, response.text)
                payload = jwt.decode(response.json()['access_token'], crypto.get_public_key_pem(),
                                     algorithms=['RS256'], issuer='manufacturing-client',
                                     audience='manufacturing-services')
                self.assertEqual(payload['sub'], 'service:order-service')
                self.assertEqual(payload['service_name'], 'order-service')
                self.assertEqual(payload['roles'], [])
            with patch.dict(os.environ, {'ORDER_SERVICE_AUTH_SECRET': ''}):
                response = await client.post('/auth/service-token', auth=('order-service', 'anything'))
                self.assertEqual(response.status_code, 503)


if __name__ == '__main__':
    unittest.main()
