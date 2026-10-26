"""Domain choreography tests. Run each service in a separate Python process."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

SERVICE = os.getenv("DOMAIN_SERVICE", "order")
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

    async def test_failed_handler_rolls_back_inbox_and_derived_events(self):
        async def handler(db, event_type, payload):
            await events.record_event(db, "derived.fact", {}, "derived:1")
            raise RuntimeError("crash before commit")
        with self.assertRaises(RuntimeError):
            await events.dispatch("original:1", "original.fact", {}, handler)
        async with database.SessionLocal() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(events.Inbox)), 0)
            self.assertEqual(await db.scalar(select(func.count()).select_from(events.Outbox)), 0)
        handler = AsyncMock()
        await events.dispatch("original:1", "original.fact", {}, handler)
        await events.dispatch("original:1", "original.fact", {}, handler)
        handler.assert_awaited_once()

    async def test_outbox_keeps_events_on_broker_failure_then_confirms_once(self):
        async with database.SessionLocal() as db:
            await events.record_event(db, "fact.first", {}, "first")
            await events.record_event(db, "fact.second", {}, "second")
            await db.commit()
        exchange = AsyncMock()
        exchange.publish.side_effect = RuntimeError("broker down")
        with self.assertRaises(RuntimeError):
            await events.flush_outbox(exchange)
        async with database.SessionLocal() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(events.Outbox).where(
                events.Outbox.published.is_(False))), 2)
        exchange.publish.reset_mock(side_effect=True)
        await events.flush_outbox(exchange)
        self.assertEqual([c.kwargs["routing_key"] for c in exchange.publish.await_args_list],
                         ["fact.first", "fact.second"])
        await events.flush_outbox(exchange)
        self.assertEqual(exchange.publish.await_count, 2)

    @unittest.skipUnless(SERVICE in ("order", "payment"), "local catalogue subscribers")
    async def test_client_facts_build_idempotent_local_catalogue(self):
        await self.send("client.created", {"client_id": 1})
        await self.send("clients.catalogue.published", {"client_ids": [1, 2]})
        async with database.SessionLocal() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(models.KnownClient)), 2)

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
    async def test_order_publishes_facts_and_observes_full_lifecycle(self):
        order_id = await self.create_order()
        self.assertEqual(await self.facts(), ["order.created"])
        result = {"order_id": order_id, "client_id": 1, "amount": 20, "accepted": True}
        await self.send("payment.approved", result)
        await self.send("payment.approved", result, "duplicate")
        self.assertEqual((await self.facts()).count("order.paid"), 1)
        for piece_id in (1, 2):
            await self.send("piece.manufactured", {"piece_id": piece_id, "order_id": order_id}, str(piece_id))
        self.assertEqual((await self.facts()).count("order.manufactured"), 1)
        data = {"order_id": order_id, "client_id": 1, "delivery_id": 5}
        await self.send("delivery.created", data)
        await self.send("delivery.ready", data)
        await self.send("delivery.delivered", data)
        await self.send("piece.manufacturing.started", {"piece_id": 1, "order_id": order_id}, "late")
        async with database.SessionLocal() as db:
            order = await db.get(models.Order, order_id)
            self.assertEqual(order.status, "Delivered")
            self.assertTrue(order.payment_completed)
            self.assertEqual(order.delivery_id, 5)

    @unittest.skipUnless(SERVICE == "order", "order choreography")
    async def test_rejection_does_not_start_manufacturing_or_delivery(self):
        order_id = await self.create_order()
        await self.send("payment.rejected", {"order_id": order_id, "client_id": 1,
                        "amount": 20, "accepted": False, "reason": "No funds"})
        self.assertEqual(await self.facts(), ["order.created"])
        async with database.SessionLocal() as db:
            self.assertEqual((await db.get(models.Order, order_id)).status, "Rejected")

    @unittest.skipUnless(SERVICE == "order", "causal retries")
    async def test_early_delivery_fact_is_retried_without_losing_it(self):
        order_id = await self.create_order()
        data = {"order_id": order_id, "client_id": 1, "delivery_id": 5}
        with self.assertRaises(events.RetryEvent):
            await self.send("delivery.ready", data)
        await self.send("payment.approved", {"order_id": order_id, "client_id": 1,
                                           "amount": 20, "accepted": True})
        for piece_id in (1, 2):
            await self.send("piece.manufactured", {"piece_id": piece_id}, str(piece_id))
        await self.send("delivery.ready", data)
        async with database.SessionLocal() as db:
            self.assertEqual((await db.get(models.Order, order_id)).status, "ReadyForDelivery")

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

    @unittest.skipUnless(SERVICE == "delivery", "independent delivery subscriber")
    async def test_delivery_decides_from_order_facts_and_does_not_regress(self):
        await self.send("order.paid", PAID)
        await self.send("order.manufactured", {**PAID, "manufactured_piece_ids": [1, 2]})
        await self.send("order.paid", PAID, "late")
        async with database.SessionLocal() as db:
            rows = list((await db.scalars(select(models.Delivery))).all())
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].status, "Ready")
        self.assertEqual(await self.facts(), ["delivery.created", "delivery.ready"])

    @unittest.skipUnless(SERVICE == "delivery", "out of order facts")
    async def test_manufactured_before_paid_still_creates_one_ready_delivery(self):
        await self.send("order.manufactured", {**PAID, "manufactured_piece_ids": [1, 2]})
        await self.send("order.paid", PAID)
        async with database.SessionLocal() as db:
            self.assertEqual((await db.scalar(select(models.Delivery))).status, "Ready")
        self.assertEqual(await self.facts(), ["delivery.created", "delivery.ready"])

    @unittest.skipUnless(SERVICE == "delivery", "delivery completion outbox")
    async def test_delivery_completion_is_persisted_with_its_fact(self):
        from app import workflow
        from app.sql import schemas
        await self.send("order.manufactured", {**PAID, "manufactured_piece_ids": [1, 2]})
        async with database.SessionLocal() as db:
            delivery = await db.scalar(select(models.Delivery))
            await workflow.set_delivery_status(db, delivery.id, schemas.DeliveryStatusUpdate(status="Delivered"))
        self.assertEqual(await self.facts(), ["delivery.created", "delivery.ready", "delivery.delivered"])

    @unittest.skipUnless(SERVICE == "machine", "durable work")
    async def test_paid_fact_persists_work_and_duplicates_do_not_repeat_it(self):
        from app.async_machine import Machine
        await self.send("order.paid", PAID)
        with patch.dict(os.environ, {"MANUFACTURING_SECONDS": "0"}):
            machine = Machine()
            self.assertTrue(await machine.manufacture_one())
            self.assertTrue(await machine.manufacture_one())
            self.assertFalse(await machine.manufacture_one())
        await self.send("order.paid", PAID, "replayed")
        self.assertEqual(await machine.list_queued_pieces(), [])
        self.assertEqual((await self.facts()).count("piece.manufactured"), 2)

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

    @unittest.skipUnless(SERVICE == "client", "client transaction")
    async def test_client_and_creation_fact_are_committed_together(self):
        from app.sql import crud, schemas
        async with database.SessionLocal() as db:
            client = await crud.create_client(db, schemas.ClientCreate(name="Alice", email="alice@example.org"))
            self.assertGreater(client.id, 0)
        self.assertEqual(await self.facts(), ["client.created"])


if __name__ == "__main__":
    unittest.main()
