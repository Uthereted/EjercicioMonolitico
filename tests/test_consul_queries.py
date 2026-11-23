"""Manual checks of discovery-based reads; all outgoing HTTP calls are mocked."""
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from my_chassis.discovery import query_service


class ConsulQueryTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, response):
        client = AsyncMock()
        client.get.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client
        with patch('my_chassis.discovery.get_service_url', AsyncMock(return_value='https://10.0.0.42:8000')) as discover, \
                patch('httpx.AsyncClient', return_value=context):
            result = await query_service('client-service', '/clients/7', access_token='original-jwt')
        discover.assert_awaited_once_with('client-service')
        client.get.assert_awaited_once_with(
            'https://10.0.0.42:8000/clients/7',
            headers={'Authorization': 'Bearer original-jwt'},
            extensions={'sni_hostname': 'client-service'},
        )
        return result

    async def test_query_uses_discovered_address_and_original_jwt(self):
        response = httpx.Response(200, json={'id': 7}, request=httpx.Request('GET', 'https://example/clients/7'))
        self.assertEqual(await self.request(response), {'id': 7})

    async def test_query_preserves_auth_and_not_found_errors(self):
        for status in (401, 403, 404):
            response = httpx.Response(status, request=httpx.Request('GET', 'https://example/clients/7'))
            with self.assertRaises(HTTPException) as caught:
                await self.request(response)
            self.assertEqual(caught.exception.status_code, status)

    async def test_missing_service_returns_503_without_a_fixed_address(self):
        with patch('my_chassis.discovery.get_service_url', AsyncMock(side_effect=RuntimeError('No instances'))):
            with self.assertRaises(HTTPException) as caught:
                await query_service('client-service', '/clients/7', access_token='original-jwt')
        self.assertEqual(caught.exception.status_code, 503)


if __name__ == '__main__':
    unittest.main()
