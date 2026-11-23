"""Manual readiness checks with fake dependencies; no live databases or broker."""
import asyncio
import unittest
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI
from my_chassis.health import HealthMonitor, create_health_router


class HealthChecks(unittest.IsolatedAsyncioTestCase):
    async def wait_for_refresh(self, monitor, after=0):
        async def wait():
            while monitor.checked_at <= after:
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait(), timeout=2)

    async def service(self, checks, *, interval=3600, timeout=0.1):
        monitor = HealthMonitor(checks, interval=interval, timeout=timeout)
        context = monitor.lifespan()
        await context.__aenter__()
        self.addAsyncCleanup(context.__aexit__, None, None, None)
        await self.wait_for_refresh(monitor)
        app = FastAPI()
        app.include_router(create_health_router('example', is_ready=lambda: monitor.available))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
        self.addAsyncCleanup(client.aclose)
        return monitor, client

    async def test_no_ok_before_the_first_check(self):
        monitor = HealthMonitor({'database': lambda: True})
        self.assertFalse(monitor.available)

    async def test_health_requests_only_read_the_cached_result(self):
        database = AsyncMock(return_value=True)
        key = AsyncMock(return_value=True)
        monitor, client = await self.service({'database': database, 'public_key': key})
        checked_at = monitor.checked_at
        for _ in range(10):
            response = await client.get('/health')
            self.assertEqual(response.status_code, 200)
        database.assert_awaited_once()
        key.assert_awaited_once()
        self.assertEqual(monitor.checked_at, checked_at)

    async def test_missing_public_key_returns_503(self):
        _, client = await self.service({'database': lambda: True, 'public_key': AsyncMock(return_value=False)})
        response = await client.get('/health')
        self.assertEqual(response.status_code, 503)

    async def test_failed_dependency_returns_503(self):
        _, client = await self.service({'database': AsyncMock(side_effect=RuntimeError('Unavailable'))})
        response = await client.get('/health')
        self.assertEqual(response.status_code, 503)

    async def test_expired_result_returns_503(self):
        monitor, client = await self.service({'rabbitmq': lambda: True})
        monitor.checked_at -= 5
        response = await client.get('/health')
        self.assertEqual(response.status_code, 503)

    async def test_slow_dependency_does_not_report_ok(self):
        async def slow():
            await asyncio.sleep(1)
            return True
        _, client = await self.service({'database': slow}, timeout=0.01)
        response = await client.get('/health')
        self.assertEqual(response.status_code, 503)

    async def test_readiness_recovers_when_the_public_key_arrives(self):
        key = AsyncMock(return_value=False)
        monitor, client = await self.service({'public_key': key}, interval=0.01)
        self.assertEqual((await client.get('/health')).status_code, 503)
        checked_at = monitor.checked_at
        key.return_value = True
        await self.wait_for_refresh(monitor, after=checked_at)
        self.assertEqual((await client.get('/health')).status_code, 200)


if __name__ == '__main__':
    unittest.main()
