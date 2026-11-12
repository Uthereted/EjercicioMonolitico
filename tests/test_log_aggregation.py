"""Offline log storage and failure-gate contracts; no Docker services required."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi import HTTPException
from service_logging import LogRuntime, LogUnavailable

spec = importlib.util.spec_from_file_location('log_aggregator', ROOT / 'services/log_aggregator/main.py')
aggregator = importlib.util.module_from_spec(spec)
with patch('pymongo.MongoClient'):
    spec.loader.exec_module(aggregator)


class LogAggregationTests(unittest.IsolatedAsyncioTestCase):
    def message(self):
        payload = {'log_id': 'example-id', 'service': 'orders', 'level': 'ERROR',
                   'timestamp': '2026-11-12T10:00:00+00:00', 'message': 'Example failure'}
        return AsyncMock(body=json.dumps(payload).encode(), routing_key='orders.error')

    async def test_log_is_acknowledged_only_after_storage(self):
        message = self.message()
        collection = MagicMock()
        collection.update_one.side_effect = lambda *args, **kwargs: message.ack.assert_not_awaited()
        with patch.object(aggregator, 'logs', collection):
            await aggregator.consume_log(message)
        message.ack.assert_awaited_once()
        message.nack.assert_not_awaited()

    async def test_failed_database_write_requeues_without_acknowledgement(self):
        message = self.message()
        collection = MagicMock()
        collection.update_one.side_effect = RuntimeError('MongoDB unavailable')
        with patch.object(aggregator, 'logs', collection), patch.object(aggregator.asyncio, 'sleep', new_callable=AsyncMock):
            await aggregator.consume_log(message)
        message.ack.assert_not_awaited()
        message.nack.assert_awaited_once_with(requeue=True)
        self.assertFalse(aggregator.ready)

    async def test_business_event_cannot_be_stored_as_an_application_log(self):
        message = AsyncMock(body=json.dumps({'event_type': 'order.created', 'data': {}}).encode(),
                            routing_key='order.created')
        collection = MagicMock()
        with patch.object(aggregator, 'logs', collection):
            await aggregator.consume_log(message)
        collection.update_one.assert_not_called()
        message.reject.assert_awaited_once_with(requeue=False)

    async def test_non_admin_cannot_query_logs(self):
        with self.assertRaises(HTTPException) as raised:
            await aggregator.require_admin({'roles': ['user']})
        self.assertEqual(raised.exception.status_code, 403)
        await aggregator.require_admin({'roles': ['admin']})

    async def test_unavailable_collector_blocks_operations_before_publication(self):
        import asyncio
        instance = LogRuntime()
        instance.lock = asyncio.Lock()
        instance.http = AsyncMock()
        instance.http.get.side_effect = RuntimeError('Collector stopped')
        instance.connect = AsyncMock()
        with self.assertRaises(LogUnavailable):
            await instance.checkpoint()
        self.assertFalse(instance.available)
        instance.connect.assert_not_awaited()

    async def test_failed_publication_keeps_log_pending_for_retry(self):
        instance = LogRuntime()
        instance.exchange = AsyncMock()
        instance.exchange.publish.side_effect = RuntimeError('Broker unavailable')
        entry = {'log_id': 'retry-id', 'service': 'orders', 'level': 'ERROR', 'message': 'Keep me'}
        instance.pending.append(entry)
        with self.assertRaises(RuntimeError):
            await instance.flush()
        self.assertEqual(list(instance.pending), [entry])
        instance.exchange.publish.side_effect = None
        await instance.flush()
        self.assertEqual(list(instance.pending), [])


if __name__ == '__main__':
    unittest.main()
