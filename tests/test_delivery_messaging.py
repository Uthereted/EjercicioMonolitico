"""Run independently: python tests/test_delivery_messaging.py."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ["SQLALCHEMY_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/delivery"))

from app import workflow
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
            with patch.object(workflow.aio_pika, "connect_robust", new=AsyncMock()) as connect:
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


if __name__ == "__main__":
    unittest.main()
