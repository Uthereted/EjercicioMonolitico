"""Audit persistence contract and acknowledgement behavior without a live broker."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/event_logger'))
import main as audit


class EventAuditTests(unittest.IsolatedAsyncioTestCase):
    def message(self, event_type='order.created'):
        envelope = {'event_id': 'order.created:123', 'event_type': event_type,
                    'producer': 'order-service', 'data': {'order_id': 123}}
        return AsyncMock(body=json.dumps(envelope).encode(), routing_key=event_type)

    async def test_ack_follows_persistence_with_stable_unique_key(self):
        collection = MagicMock()
        message = self.message()
        await audit.process_event(message, collection)
        query, update = collection.update_one.call_args.args
        self.assertEqual(query, {'_id': 'order.created:123'})
        self.assertTrue(collection.update_one.call_args.kwargs['upsert'])
        self.assertEqual(update['$setOnInsert']['producer'], 'order-service')
        self.assertEqual(update['$setOnInsert']['data']['order_id'], 123)
        message.ack.assert_awaited_once()
        message.nack.assert_not_awaited()

    async def test_database_failure_is_not_acknowledged(self):
        collection = MagicMock()
        collection.update_one.side_effect = ConnectionError('MongoDB unavailable')
        message = self.message()
        with patch.object(audit.asyncio, 'sleep', new=AsyncMock()):
            await audit.process_event(message, collection)
        message.ack.assert_not_awaited()
        message.nack.assert_awaited_once_with(requeue=True)

    async def test_auth_material_is_rejected_instead_of_archived(self):
        collection = MagicMock()
        message = self.message('auth.signing-key.published')
        await audit.process_event(message, collection)
        collection.update_one.assert_not_called()
        message.reject.assert_awaited_once_with(requeue=False)


if __name__ == '__main__':
    unittest.main()
