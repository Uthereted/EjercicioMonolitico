"""Audit persistence contract and acknowledgement behavior without a live broker."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/event_logger'))
import main as audit


class EventAuditTests(unittest.IsolatedAsyncioTestCase):
    def message(self, event_type='order.created'):
        envelope = {'event_id': f'{event_type}:123', 'event_type': event_type,
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

    async def test_saga_commands_responses_and_compensations_are_archived_once(self):
        documents = {}
        collection = MagicMock()

        def upsert(query, update, **kwargs):
            documents.setdefault(query['_id'], update['$setOnInsert'])

        collection.update_one.side_effect = upsert
        topics = ('saga.payment.reserve', 'saga.payment.reserved', 'saga.payment.rejected',
                  'saga.payment.confirm', 'saga.payment.confirmed',
                  'saga.payment.release', 'saga.payment.released',
                  'saga.delivery.create', 'saga.delivery.accepted', 'saga.delivery.rejected',
                  'saga.delivery.cancel', 'saga.delivery.canceled')
        for topic in topics:
            for _ in range(2):
                message = self.message(topic)
                await audit.process_event(message, collection)
                message.ack.assert_awaited_once()
                message.reject.assert_not_awaited()
        self.assertEqual(len(documents), len(topics))
        self.assertEqual({value['event_type'] for value in documents.values()}, set(topics))

    async def test_same_audit_queue_subscribes_to_all_three_exchanges(self):
        domain = MagicMock(broker_url='amqp://test/')
        domain.subscribe = AsyncMock(return_value='audit-queue')
        commands, responses = AsyncMock(), AsyncMock()
        channel = object()
        with patch.object(audit, 'TopicTransport', side_effect=[commands, responses]) as transport:
            queue = await audit.subscribe_audit(channel, domain)
        self.assertEqual(queue, 'audit-queue')
        domain.subscribe.assert_awaited_once_with(channel, 'audit.domain', audit.TOPICS)
        self.assertEqual(transport.call_args_list, [
            call('amqp://test/', 'manufacturing.commands'),
            call('amqp://test/', 'manufacturing.saga.responses'),
        ])
        for subscriber in (commands, responses):
            subscriber.subscribe.assert_awaited_once_with(channel, 'audit.domain', ('saga.#',))
        definitions = json.loads((Path(__file__).resolve().parents[1] /
                                  'infrastructure/rabbitmq/domain-definitions.json').read_text())
        sources = {binding['source'] for binding in definitions['bindings']
                   if binding['destination'] == 'audit.domain' and binding['routing_key'] == 'saga.#'}
        self.assertEqual(sources, set(audit.SAGA_EXCHANGES))

    async def test_auth_material_is_rejected_instead_of_archived(self):
        collection = MagicMock()
        message = self.message('auth.signing-key.published')
        await audit.process_event(message, collection)
        collection.update_one.assert_not_called()
        message.reject.assert_awaited_once_with(requeue=False)


if __name__ == '__main__':
    unittest.main()
