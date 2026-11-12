"""Offline saga history tests. Created for manual execution; no MongoDB or RabbitMQ needed."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'services/order'))
os.environ['SQLALCHEMY_DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'

from fastapi import HTTPException
from sqlalchemy import select
with patch('pymongo.MongoClient'):
    from app import events, saga, saga_history
from app.sql import database, models

spec = importlib.util.spec_from_file_location('saga_audit', ROOT / 'services/event_logger/main.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class SagaHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        gate = patch('service_logging.runtime.wait_available', new_callable=AsyncMock)
        gate.start()
        self.addCleanup(gate.stop)
        async with database.engine.begin() as connection:
            await connection.run_sync(database.Base.metadata.drop_all)
        await database.initialize()

    async def asyncTearDown(self):
        await database.engine.dispose()

    async def create_saga(self):
        async with database.SessionLocal() as db:
            order = models.Order(number_of_pieces=1, client_id=1, address='01001 Vitoria',
                                 total_price=10, status='PendingPayment')
            db.add(order)
            await db.flush()
            coordinator = await saga.start(db, order)
            identifiers = coordinator.id, order.id
            await db.commit()
            return identifiers

    async def entries(self):
        async with database.SessionLocal() as db:
            rows = await db.scalars(select(events.Outbox.payload).where(
                events.Outbox.event_type == saga_history.EVENT_TYPE).order_by(events.Outbox.id))
            return [json.loads(row) for row in rows]

    async def test_real_transitions_are_recorded_once_even_with_duplicate_responses(self):
        saga_id, order_id = await self.create_saga()
        payment = {'saga_id': saga_id, 'order_id': order_id, 'client_id': 1,
                   'amount': 10, 'accepted': True}
        delivery = {'saga_id': saga_id, 'order_id': order_id, 'client_id': 1,
                    'delivery_id': 1, 'accepted': True}
        for event_id, event_type, payload in (
            ('p1', 'saga.payment.reserved', payment),
            ('p2', 'saga.payment.reserved', payment),
            ('d1', 'saga.delivery.accepted', delivery),
            ('c1', 'saga.payment.confirmed', payment),
            ('c2', 'saga.payment.confirmed', payment),
        ):
            await events.dispatch(event_id, event_type, payload, saga.handle_event)
        entries = await self.entries()
        self.assertEqual([entry['state'] for entry in entries], [
            'PendingPaymentAndDelivery', 'PendingDelivery', 'PendingConfirmation', 'Approved'])
        self.assertEqual([entry['sequence'] for entry in entries], [1, 2, 3, 4])
        self.assertTrue(all(not entry['initial_snapshot'] for entry in entries))

    async def test_failed_history_publication_rolls_back_the_state_and_version(self):
        saga_id, _ = await self.create_saga()
        with self.assertRaises(RuntimeError):
            async with database.SessionLocal() as db:
                coordinator = await db.get(models.OrderSaga, saga_id)
                with patch.object(saga_history, 'record_event', side_effect=RuntimeError('Outbox failed')):
                    await saga_history.change_state(db, coordinator, 'PendingDelivery')
                await db.commit()
        async with database.SessionLocal() as db:
            coordinator = await db.get(models.OrderSaga, saga_id)
            self.assertEqual(coordinator.state, 'PendingPaymentAndDelivery')
            self.assertEqual(coordinator.history_version, 1)
        self.assertEqual(len(await self.entries()), 1)

    async def test_existing_saga_gets_one_current_snapshot_without_fake_previous_states(self):
        async with database.SessionLocal() as db:
            order = models.Order(number_of_pieces=1, client_id=1, total_price=10)
            db.add(order)
            await db.flush()
            db.add(models.OrderSaga(order_id=order.id, state='Suspended'))
            await db.commit()
        for _ in range(2):
            async with database.SessionLocal() as db:
                await saga_history.snapshot_existing(db)
                await db.commit()
        entries = await self.entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['state'], 'Suspended')
        self.assertTrue(entries[0]['initial_snapshot'])
        self.assertIsNone(entries[0]['previous_state'])

    async def test_history_write_failure_requeues_even_when_audit_write_succeeded(self):
        saga_id, _ = await self.create_saga()
        entry = (await self.entries())[0]
        envelope = {'event_id': f'{saga_history.EVENT_TYPE}:{saga_id}:1',
                    'event_type': saga_history.EVENT_TYPE, 'producer': 'order-service', 'data': entry}
        message = AsyncMock(body=json.dumps(envelope).encode(), routing_key=saga_history.EVENT_TYPE)
        collection = MagicMock()
        collection.database.get_collection.return_value.update_one.side_effect = RuntimeError('Mongo write failed')
        with patch.object(audit.asyncio, 'sleep', new_callable=AsyncMock):
            await audit.process_event(message, collection)
        collection.update_one.assert_called_once()
        message.ack.assert_not_awaited()
        message.nack.assert_awaited_once_with(requeue=True)

    async def test_replayed_history_event_keeps_one_mongodb_record(self):
        saga_id, _ = await self.create_saga()
        entry = (await self.entries())[0]
        envelope = {'event_id': f'{saga_history.EVENT_TYPE}:{saga_id}:1',
                    'event_type': saga_history.EVENT_TYPE, 'producer': 'order-service', 'data': entry}
        collection = MagicMock()
        stored = {}
        def upsert(query, update, **kwargs):
            stored.setdefault(query['_id'], update['$setOnInsert'])
        collection.database.get_collection.return_value.update_one.side_effect = upsert
        audit.store_event(collection, envelope)
        audit.store_event(collection, envelope)
        self.assertEqual(len(stored), 1)
        self.assertEqual(next(iter(stored.values()))['state'], 'PendingPaymentAndDelivery')

    async def test_only_admin_can_query_history(self):
        with self.assertRaises(HTTPException) as raised:
            await saga_history.require_admin({'roles': ['user']})
        self.assertEqual(raised.exception.status_code, 403)
        await saga_history.require_admin({'roles': ['admin']})

    async def test_unavailable_history_database_returns_503(self):
        collection = MagicMock()
        collection.aggregate.side_effect = RuntimeError('MongoDB unavailable')
        with patch.object(saga_history, 'history', collection):
            with self.assertRaises(HTTPException) as raised:
                await saga_history.read_history({}, 10, 0, latest=True)
        self.assertEqual(raised.exception.status_code, 503)


if __name__ == '__main__':
    unittest.main()
