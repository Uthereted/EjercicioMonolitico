"""Domain choreography tests. Run each service in a separate Python process."""
import argparse
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--service', choices=('order', 'payment', 'delivery', 'machine'), default='order')
options, remaining = parser.parse_known_args()
SERVICE = options.service
sys.argv = [sys.argv[0], *remaining]
os.environ["SQLALCHEMY_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / SERVICE))
from sqlalchemy import select, func
from sqlalchemy.dialects.sqlite import insert
from app import events, messaging
from app.sql import database, models

PAID = {"order_id": 1, "client_id": 1, "amount": 20, "address": "Madrid", "piece_ids": [1, 2]}


class DomainEventTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with database.engine.begin() as conn:
            await conn.run_sync(database.Base.metadata.drop_all)
            await conn.run_sync(database.Base.metadata.create_all)

    async def asyncTearDown(self):
        await database.engine.dispose()

    async def send(self, event_type, payload, suffix="1"):
        await events.dispatch(f"{event_type}:{suffix}", event_type, payload, messaging.handle_event)

    async def facts(self):
        async with database.SessionLocal() as db:
            return list((await db.scalars(select(events.Outbox.event_type).order_by(events.Outbox.id))).all())

    async def create_order(self):
        from app.routers import main_router
        from app.sql import schemas
        async with database.SessionLocal() as db:
            await db.execute(insert(models.KnownClient).values(id=1))
            await db.commit()
            order = await main_router.create_order(schemas.OrderPost(
                client_id=1, number_of_pieces=2, address="Madrid"), db, token_payload={"roles": ["admin"]})
            return order.id

    @unittest.skipUnless(SERVICE == "order", "order choreography")
    async def test_rejection_does_not_start_manufacturing_or_delivery(self):
        order_id = await self.create_order()
        await self.send("payment.rejected", {"order_id": order_id, "client_id": 1,
                        "amount": 20, "accepted": False, "reason": "No funds"})
        self.assertEqual(await self.facts(), ["order.created"])
        async with database.SessionLocal() as db:
            self.assertEqual((await db.get(models.Order, order_id)).status, "Rejected")

    @unittest.skipUnless(SERVICE == "payment", "payment decision")
    async def test_payment_result_and_charge_are_atomic_and_idempotent(self):
        from app.sql import crud
        async with database.SessionLocal() as db:
            await crud.deposit(db, 1, 100)
        payload = {"order_id": 1, "client_id": 1, "amount": 20}
        await self.send("order.created", payload)
        await self.send("order.created", payload, "redelivery")
        async with database.SessionLocal() as db:
            account = await crud.get_account_by_client_id(db, 1)
            self.assertEqual(account.balance, 80)
            self.assertEqual(await db.scalar(select(func.count()).select_from(models.Transaction).where(
                models.Transaction.type == "Charge")), 1)
        self.assertEqual(await self.facts(), ["payment.approved"])

    @unittest.skipUnless(SERVICE == "payment", "retained negative outcomes")
    async def test_later_deposit_does_not_change_rejected_payment(self):
        from app.sql import crud
        payload = {"order_id": 1, "client_id": 1, "amount": 20}
        await self.send("order.created", payload)
        async with database.SessionLocal() as db:
            await crud.deposit(db, 1, 100)
        await self.send("order.created", payload, "retry")
        self.assertEqual(await self.facts(), ["payment.rejected"])
        async with database.SessionLocal() as db:
            self.assertEqual((await crud.get_account_by_client_id(db, 1)).balance, 100)

    @unittest.skipUnless(SERVICE == "delivery", "out of order facts")
    async def test_manufactured_before_paid_still_creates_one_ready_delivery(self):
        await self.send("order.manufactured", {**PAID, "manufactured_piece_ids": [1, 2]})
        await self.send("order.paid", PAID)
        async with database.SessionLocal() as db:
            self.assertEqual((await db.scalar(select(models.Delivery))).status, "Ready")
        self.assertEqual(await self.facts(), ["delivery.created", "delivery.ready"])

    @unittest.skipUnless(SERVICE == "machine", "restart recovery")
    async def test_new_machine_resumes_local_work_without_another_service(self):
        from app.async_machine import Machine
        await self.send("order.paid", PAID)
        async with database.SessionLocal() as db:
            piece = await db.get(models.WorkPiece, 1)
            piece.status = "Manufacturing"
            await db.commit()
        with patch.dict(os.environ, {"MANUFACTURING_SECONDS": "0"}):
            restarted = Machine()
            self.assertTrue(await restarted.manufacture_one())
        async with database.SessionLocal() as db:
            self.assertEqual((await db.get(models.WorkPiece, 1)).status, "Manufactured")

if __name__ == "__main__":
    unittest.main()
