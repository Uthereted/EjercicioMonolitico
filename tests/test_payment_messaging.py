"""Run independently: python tests/test_payment_messaging.py."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ["SQLALCHEMY_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/payment"))

from sqlalchemy import select, func
from app import messaging
from app.sql import database, models, crud


class PaymentMessagingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with database.engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.drop_all)
            await conn.run_sync(models.Base.metadata.create_all)
        self.event = messaging.OrderCreated(order_id=1, client_id=1, amount=20)
        self.publish_patch = patch.object(messaging, "publish_payment_result", new_callable=AsyncMock)
        self.publish_result = self.publish_patch.start()

    async def asyncTearDown(self):
        self.publish_patch.stop()
        await database.engine.dispose()

    async def test_redelivery_after_callback_failure_does_not_charge_twice(self):
        async with database.SessionLocal() as db:
            await crud.deposit(db, 1, 100)
        self.publish_result.side_effect = ConnectionError("RabbitMQ unavailable")
        with self.assertRaises(ConnectionError):
            await messaging.handle_order_created(self.event)
        self.publish_result.side_effect = None
        await messaging.handle_order_created(self.event)
        async with database.SessionLocal() as db:
            account = await crud.get_account_by_client_id(db, 1)
            self.assertEqual(account.balance, 80)
            self.assertEqual(await db.scalar(select(func.count()).select_from(models.Transaction).where(
                models.Transaction.type == "Charge")), 1)
        self.assertTrue(self.publish_result.await_args.args[0].accepted)

    async def test_rejected_result_survives_redelivery_and_later_deposit(self):
        await messaging.handle_order_created(self.event)
        async with database.SessionLocal() as db:
            await crud.deposit(db, 1, 100)
        await messaging.handle_order_created(self.event)
        async with database.SessionLocal() as db:
            account = await crud.get_account_by_client_id(db, 1)
            self.assertEqual(account.balance, 100)
        self.assertFalse(self.publish_result.await_args.args[0].accepted)


if __name__ == "__main__":
    unittest.main()
