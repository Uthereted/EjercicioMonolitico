"""Run independently: python tests/test_delivery_messaging.py."""
import json
import os
import sys
import unittest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

os.environ["SQLALCHEMY_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/delivery"))

from app import workflow, messaging
from my_chassis import messaging as chassis_messaging
from app.sql import database, models


class DeliveryMessagingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with database.engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.drop_all)
            await conn.run_sync(models.Base.metadata.create_all)

    async def asyncTearDown(self):
        await database.engine.dispose()

    async def check_publication(self, failure=False, status="Delivered"):
        async with database.SessionLocal() as db:
            delivery = models.Delivery(order_id=12, client_id=1, address="Madrid", status=status)
            db.add(delivery)
            await db.commit()
            with patch.object(chassis_messaging.aio_pika, "connect_robust", new=AsyncMock()) as connect:
                exchange = connect.return_value.channel.return_value.declare_exchange.return_value
                if failure:
                    exchange.publish.side_effect = RuntimeError("broker unavailable")
                await workflow.notify_order(db, delivery)
                if status != "Delivered":
                    connect.assert_not_called()
                    return
                self.assertEqual(delivery.order_notified, not failure)
                message = exchange.publish.call_args.args[0]
                self.assertEqual(json.loads(message.body), {
                    "delivery_id": delivery.id, "order_id": 12, "status": "Delivered",
                })
                self.assertEqual(exchange.publish.call_args.kwargs["routing_key"], "delivery.status.update")
                if failure:
                    exchange.publish.side_effect = None
                    await workflow.notify_order(db, delivery)
                    self.assertTrue(delivery.order_notified)
                count = exchange.publish.await_count
                await workflow.notify_order(db, delivery)
                self.assertEqual(exchange.publish.await_count, count)

    async def test_confirmed_publication_is_not_repeated(self):
        await self.check_publication()

    async def test_failed_publication_can_be_retried(self):
        await self.check_publication(failure=True)

    async def test_ready_does_not_publish(self):
        await self.check_publication(status="Ready")

    async def test_sent_does_not_publish(self):
        await self.check_publication(status="Sent")

    async def test_order_delivery_ready_changes_pending_to_ready(self):
        async with database.SessionLocal() as db:
            delivery = models.Delivery(
                order_id=12,
                client_id=1,
                address="Madrid",
                status="Pending",
                workflow_managed=True,
            )
            db.add(delivery)
            await db.commit()
            await db.refresh(delivery)

            event = messaging.OrderDeliveryReady(
                order_id=12,
                delivery_id=delivery.id,
                status="Ready",
            )

            await messaging.handle_order_delivery_ready(db, event)

            self.assertEqual(delivery.status, "Ready")

    async def test_duplicate_order_delivery_ready_event_is_safe(self):
        async with database.SessionLocal() as db:
            delivery = models.Delivery(
                order_id=12,
                client_id=1,
                address="Madrid",
                status="Pending",
                workflow_managed=True,
            )
            db.add(delivery)
            await db.commit()
            await db.refresh(delivery)

            event = messaging.OrderDeliveryReady(
                order_id=12,
                delivery_id=delivery.id,
                status="Ready",
            )

            await messaging.handle_order_delivery_ready(db, event)
            await messaging.handle_order_delivery_ready(db, event)

            self.assertEqual(delivery.status, "Ready")

    async def test_unknown_delivery_is_rejected(self):
        async with database.SessionLocal() as db:
            event = messaging.OrderDeliveryReady(
                order_id=12,
                delivery_id=999,
                status="Ready",
            )

            with self.assertRaises(ValueError):
                await messaging.handle_order_delivery_ready(db, event)

    async def test_event_with_wrong_order_is_rejected(self):
        async with database.SessionLocal() as db:
            delivery = models.Delivery(
                order_id=12,
                client_id=1,
                address="Madrid",
                status="Pending",
                workflow_managed=True,
            )
            db.add(delivery)
            await db.commit()
            await db.refresh(delivery)

            event = messaging.OrderDeliveryReady(
                order_id=999,
                delivery_id=delivery.id,
                status="Ready",
            )

            with self.assertRaises(ValueError):
                await messaging.handle_order_delivery_ready(db, event)

    async def test_valid_ready_event_is_acknowledged(self):
        async with database.SessionLocal() as db:
            delivery = models.Delivery(
                order_id=12,
                client_id=1,
                address="Madrid",
                status="Pending",
                workflow_managed=True,
            )
            db.add(delivery)
            await db.commit()
            await db.refresh(delivery)

            message = AsyncMock(
                body=(
                    f'{{"order_id":12,"delivery_id":{delivery.id},'
                    '"status":"Ready"}'
                ).encode()
            )

        async def stream():
            yield message
            raise asyncio.CancelledError

        with patch(
            "app.messaging.aio_pika.connect_robust",
            new=AsyncMock(),
        ) as connect:
            channel = connect.return_value.channel.return_value
            queue = channel.declare_queue.return_value
            queue.iterator = MagicMock()
            queue.iterator.return_value.__aenter__.return_value = stream()

            with self.assertRaises(asyncio.CancelledError):
                await messaging.consume_delivery_events()

        message.ack.assert_awaited_once()
        message.reject.assert_not_awaited()
        message.nack.assert_not_awaited()

        async with database.SessionLocal() as db:
            saved = await db.get(models.Delivery, delivery.id)
            self.assertEqual(saved.status, "Ready")

    async def test_invalid_ready_event_is_rejected(self):
        message = AsyncMock(
            body=b'{"order_id": 0, "delivery_id": 1, "status": "Unknown"}'
        )

        async def stream():
            yield message
            raise asyncio.CancelledError

        with patch(
            "app.messaging.aio_pika.connect_robust",
            new=AsyncMock(),
        ) as connect:
            channel = connect.return_value.channel.return_value
            queue = channel.declare_queue.return_value
            queue.iterator = MagicMock()
            queue.iterator.return_value.__aenter__.return_value = stream()

            with self.assertRaises(asyncio.CancelledError):
                await messaging.consume_delivery_events()

        message.reject.assert_awaited_once_with(requeue=False)
        message.ack.assert_not_awaited()
        message.nack.assert_not_awaited()

    async def test_processing_failure_is_requeued(self):
        message = AsyncMock(
            body=b'{"order_id": 12, "delivery_id": 1, "status": "Ready"}'
        )

        async def stream():
            yield message
            raise asyncio.CancelledError

        with patch(
            "app.messaging.handle_order_delivery_ready",
            new=AsyncMock(side_effect=RuntimeError("database unavailable")),
        ), patch(
            "app.messaging.aio_pika.connect_robust",
            new=AsyncMock(),
        ) as connect:
            channel = connect.return_value.channel.return_value
            queue = channel.declare_queue.return_value
            queue.iterator = MagicMock()
            queue.iterator.return_value.__aenter__.return_value = stream()

            with self.assertRaises(asyncio.CancelledError):
                await messaging.consume_delivery_events()

        message.nack.assert_awaited_once_with(requeue=True)
        message.ack.assert_not_awaited()
        message.reject.assert_not_awaited()

if __name__ == "__main__":
    unittest.main()
