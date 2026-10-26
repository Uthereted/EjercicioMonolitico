"""Run separately with AUTH_CALLER=order or payment to verify internal client reads."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException

SERVICE = os.getenv('AUTH_CALLER', 'order')
if SERVICE not in ('order', 'payment'):
    raise ValueError('AUTH_CALLER must be order or payment')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services' / SERVICE))
os.environ['SQLALCHEMY_DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
if SERVICE == 'order':
    from app import workflow as target
    lookup = target.validate_client
else:
    from app import dependencies as target
    lookup = target.client_exists


class AuthenticatedClientLookupTests(unittest.IsolatedAsyncioTestCase):
    async def check_lookup(self, *, client_status=200, auth_status=200, secret='test-secret'):
        requests = []

        def respond(request):
            requests.append(request)
            if request.url.path == '/auth/service-token':
                self.assertEqual(request.method, 'POST')
                expected = httpx.BasicAuth(f'{SERVICE}-service', secret)
                auth_request = next(expected.auth_flow(httpx.Request('POST', str(request.url))))
                self.assertEqual(request.headers['Authorization'], auth_request.headers['Authorization'])
                return httpx.Response(auth_status, json={'access_token': 'service-jwt'})
            self.assertEqual(request.url.path, '/clients/123')
            self.assertEqual(request.headers['Authorization'], 'Bearer service-jwt')
            return httpx.Response(client_status, json={'id': 123})

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            with patch.dict(os.environ, {f'{SERVICE.upper()}_SERVICE_AUTH_SECRET': secret}):
                with patch.object(target.httpx, 'AsyncClient', return_value=client):
                    result = await lookup(123)
        finally:
            await client.aclose()
        return result, requests

    async def test_lookup_sends_named_service_token(self):
        result, requests = await self.check_lookup()
        self.assertEqual(len(requests), 2)
        if SERVICE == 'payment':
            self.assertTrue(result)

    async def test_missing_client_preserves_not_found_behavior(self):
        if SERVICE == 'order':
            with self.assertRaises(HTTPException) as error:
                await self.check_lookup(client_status=404)
            self.assertEqual(error.exception.status_code, 404)
        else:
            result, _ = await self.check_lookup(client_status=404)
            self.assertFalse(result)

    async def test_rejected_credentials_do_not_fall_back_to_anonymous_read(self):
        error_type = HTTPException if SERVICE == 'order' else httpx.HTTPStatusError
        with self.assertRaises(error_type) as error:
            await self.check_lookup(auth_status=401)
        if SERVICE == 'order':
            self.assertEqual(error.exception.status_code, 503)

    async def test_missing_service_secret_fails_without_anonymous_read(self):
        error_type = HTTPException if SERVICE == 'order' else ValueError
        with self.assertRaises(error_type):
            await self.check_lookup(secret='')


if __name__ == '__main__':
    unittest.main()
