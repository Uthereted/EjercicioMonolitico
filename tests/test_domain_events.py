"""Domain choreography tests. Run each service in a separate Python process."""
import argparse
import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--service', choices=('order', 'payment', 'delivery', 'machine', 'client'), default='order')
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
        await database.initialize()

    async def asyncTearDown(self):
        await database.engine.dispose()

    async def send(self, event_type, payload, suffix="1"):
        await events.dispatch(f"{event_type}:{suffix}", event_type, payload, messaging.handle_event)

    async def test_database_initialization_preserves_existing_inbox(self):
        async with database.SessionLocal() as db:
            db.add(events.Inbox(event_id="keep-after-initialization"))
            await db.commit()
        await database.initialize()
        async with database.SessionLocal() as db:
            self.assertIsNotNone(await db.get(events.Inbox, "keep-after-initialization"))

    async def facts(self):
        async with database.SessionLocal() as db:
            return list((await db.scalars(select(events.Outbox.event_type).order_by(events.Outbox.id))).all())

    async def send_saga(self, event_type, payload, suffix="1"):
        from app import saga
        await events.dispatch(f"{event_type}:{suffix}", event_type, payload, saga.handle_event)

    @unittest.skipUnless(SERVICE == "client", "client address publication")
    async def test_client_address_is_published_on_creation_and_periodic_task_only_publishes_key(self):
        from app.sql import crud, schemas
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            schemas.ClientCreate(name="Test", email="test@example.org")
        with self.assertRaises(ValidationError):
            schemas.ClientCreate(name="Test", email="test@example.org", address="Calle Mayor 10", postal_code="1234")
        async with database.SessionLocal() as db:
            client = await crud.create_client(db, schemas.ClientCreate(
                name="Test", email="test@example.org", address="Calle Mayor 10", postal_code="01001"))
            self.assertEqual(schemas.ClientResponse.model_validate(client).postal_code, "01001")
        with patch.object(messaging.crypto, "get_public_key_pem", return_value="public-key"), \
                patch.object(messaging.asyncio, "sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await messaging.publish_signing_key()
        async with database.SessionLocal() as db:
            created = json.loads(await db.scalar(select(events.Outbox.payload).where(
                events.Outbox.event_type == "client.created")))
            self.assertEqual(created, {"client_id": client.id, "address": "Calle Mayor 10", "postal_code": "01001"})
            key = json.loads(await db.scalar(select(events.Outbox.payload).where(
                events.Outbox.event_type == "auth.signing-key.published")))
            self.assertEqual(key, {"public_key": "public-key"})
        self.assertEqual(await self.facts(), ["client.created", "auth.signing-key.published"])

    @unittest.skipUnless(SERVICE == "order", "client address projection")
    async def test_order_uses_client_address_from_creation_event_without_http(self):
        from app.routers import main_router
        from app.sql import schemas
        from fastapi import HTTPException
        await self.send("client.created", {
            "client_id": 1, "address": "Calle Mayor 10", "postal_code": "01001"})
        await self.send("client.created", {"client_id": 1}, "legacy")
        await self.send("client.created", {"client_id": 2}, "second")
        async with database.SessionLocal() as db:
            known = await db.get(models.KnownClient, 1)
            self.assertEqual(known.postal_code, "01001")
            order = await main_router.create_order(schemas.OrderPost(client_id=1, number_of_pieces=1),
                                                  db, token_payload={"roles": ["admin"]})
            self.assertEqual(order.address, "Calle Mayor 10, 01001")
            command = json.loads(await db.scalar(select(events.Outbox.payload).where(
                events.Outbox.event_type == "saga.delivery.create")))
            self.assertEqual(command["address"], order.address)
            with self.assertRaises(HTTPException) as caught:
                await main_router.create_order(schemas.OrderPost(client_id=2, number_of_pieces=1),
                                               db, token_payload={"roles": ["admin"]})
            self.assertEqual(caught.exception.status_code, 409)

    @unittest.skipUnless(SERVICE == "payment", "saga compensation")
    async def test_release_restores_only_reserved_balance_once_and_rolls_back_on_failure(self):
        from app import saga
        from app.sql import crud
        async with database.SessionLocal() as db:
            await crud.deposit(db, 1, 100)
        command = {"saga_id": 1, "order_id": 1, "client_id": 1, "amount": 60}
        await self.send_saga("saga.payment.reserve", command)
        with patch.object(saga, "record_event", side_effect=RuntimeError("outbox unavailable")):
            with self.assertRaises(RuntimeError):
                await self.send_saga("saga.payment.release", command)
        async with database.SessionLocal() as db:
            self.assertEqual((await crud.get_account_by_client_id(db, 1)).balance, 40)
            self.assertEqual((await db.get(models.PaymentReservation, 1)).state, "Reserved")
        await self.send_saga("saga.payment.release", command)
        await self.send_saga("saga.payment.release", command, "retry")
        await self.send_saga("saga.payment.reserve", command, "late")
        rejected = {**command, "saga_id": 2, "order_id": 2, "amount": 200}
        await self.send_saga("saga.payment.reserve", rejected, "second")
        await self.send_saga("saga.payment.release", rejected, "second")
        async with database.SessionLocal() as db:
            self.assertEqual((await crud.get_account_by_client_id(db, 1)).balance, 100)
            self.assertEqual((await db.get(models.PaymentReservation, 1)).state, "Released")
            self.assertEqual((await db.get(models.PaymentReservation, 2)).state, "Released")
            self.assertEqual(await db.scalar(select(func.count()).select_from(models.Transaction).where(
                models.Transaction.type == "Charge")), 0)

    @unittest.skipUnless(SERVICE == "delivery", "saga compensation")
    async def test_cancel_is_atomic_and_prevents_late_reactivation(self):
        from app import saga
        command = {"saga_id": 1, "order_id": 1, "client_id": 1, "address": "48001 Bilbao"}
        await self.send_saga("saga.delivery.create", command)
        with patch.object(saga, "record_event", side_effect=RuntimeError("outbox unavailable")):
            with self.assertRaises(RuntimeError):
                await self.send_saga("saga.delivery.cancel", command)
        async with database.SessionLocal() as db:
            self.assertEqual((await db.scalar(select(models.Delivery))).status, "Provisional")
        await self.send_saga("saga.delivery.cancel", command)
        await self.send_saga("saga.delivery.cancel", command, "retry")
        await self.send_saga("saga.delivery.create", command, "late")
        async with database.SessionLocal() as db:
            self.assertEqual((await db.scalar(select(models.Delivery))).status, "Canceled")
            self.assertEqual(await db.scalar(select(func.count()).select_from(models.Delivery)), 1)
        self.assertEqual((await self.facts()).count("saga.delivery.canceled"), 1)

    @unittest.skipUnless(SERVICE == "order", "saga compensation coordination")
    async def test_suspension_waits_for_both_compensations_in_either_order(self):
        from app import workflow
        for payment_first in (True, False):
            order_id = await self.create_order()
            async with database.SessionLocal() as db:
                saga_id = (await db.scalar(select(models.OrderSaga).where(
                    models.OrderSaga.order_id == order_id))).id
            common = {"saga_id": saga_id, "order_id": order_id, "client_id": 1}
            payment = {**common, "amount": 20, "accepted": True}
            delivery = {**common, "delivery_id": order_id, "accepted": True}
            await self.send_saga("saga.delivery.rejected", {
                **delivery, "accepted": False, "reason": "Outside coverage"}, str(order_id))
            await self.send_saga("saga.payment.reserved", payment, str(order_id))
            await workflow.bootstrap()
            replies = [("saga.payment.released", payment), ("saga.delivery.canceled", delivery)]
            if not payment_first:
                replies.reverse()
            await self.send_saga(*replies[0], suffix=str(order_id))
            await self.send_saga(*replies[0], suffix=f"duplicate-{order_id}")
            async with database.SessionLocal() as db:
                self.assertEqual((await db.get(models.Order, order_id)).status, "Compensating")
            await workflow.bootstrap()
            await self.send_saga(*replies[1], suffix=str(order_id))
            await self.send_saga("saga.payment.reserved", payment, f"late-{order_id}")
            await workflow.bootstrap()
            async with database.SessionLocal() as db:
                order = await db.get(models.Order, order_id)
                self.assertEqual(order.status, "Suspended")
                self.assertFalse(order.payment_completed)
                self.assertEqual(order.integration_error, "Outside coverage")
                self.assertEqual((await db.get(models.OrderSaga, saga_id)).state, "Suspended")
        facts = await self.facts()
        self.assertEqual(facts.count("saga.payment.release"), 2)
        self.assertEqual(facts.count("saga.delivery.cancel"), 2)
        self.assertNotIn("saga.payment.confirm", facts)
        self.assertNotIn("order.paid", facts)

    @unittest.skipUnless(SERVICE in ("payment", "delivery"), "saga atomicity")
    async def test_saga_response_failure_rolls_back_participant_changes(self):
        from app import saga
        command = {"saga_id": 1, "order_id": 1, "client_id": 1, "amount": 20,
                   "address": "Calle Mayor 10, 48001 Bilbao"}
        topic = "saga.payment.reserve" if SERVICE == "payment" else "saga.delivery.create"
        if SERVICE == "payment":
            from app.sql import crud
            async with database.SessionLocal() as db:
                await crud.deposit(db, 1, 100)
        with patch.object(saga, "record_event", side_effect=RuntimeError("outbox unavailable")):
            with self.assertRaises(RuntimeError):
                await self.send_saga(topic, command)
        async with database.SessionLocal() as db:
            if SERVICE == "payment":
                self.assertEqual((await crud.get_account_by_client_id(db, 1)).balance, 100)
                self.assertIsNone(await db.get(models.PaymentReservation, 1))
            else:
                self.assertEqual(await db.scalar(select(func.count()).select_from(models.Delivery)), 0)
        await self.send_saga(topic, command)
        self.assertEqual(len(await self.facts()), 1)

    @unittest.skipUnless(SERVICE == "delivery", "saga coverage")
    async def test_saga_delivery_validates_postal_coverage_and_deduplicates(self):
        from app import workflow
        from app.sql import schemas
        from fastapi import HTTPException
        addresses = ["01001 Vitoria", "20001 Donostia", "48001 Bilbao", "28001 Madrid",
                     "Bilbao", "148001", "48001 Bilbao 28001 Madrid"]
        for index, address in enumerate(addresses, 1):
            with self.subTest(address=address):
                command = {"saga_id": index, "order_id": index, "client_id": 1, "address": address}
                await self.send_saga("saga.delivery.create", command, str(index))
                await self.send_saga("saga.delivery.create", command, f"retry-{index}")
                async with database.SessionLocal() as db:
                    result = await db.get(models.DeliverySagaResult, index)
                    self.assertEqual(result.accepted, index <= 3)
                    delivery = await db.get(models.Delivery, result.delivery_id)
                    self.assertEqual(delivery.status, "Provisional" if result.accepted else "Suspended")
                    with self.assertRaises(HTTPException) as caught:
                        await workflow.set_delivery_status(db, delivery.id, schemas.DeliveryStatusUpdate(status="Ready"))
                    self.assertEqual(caught.exception.status_code, 409)
        async with database.SessionLocal() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(models.Delivery)), len(addresses))
        self.assertEqual(await self.facts(), ["saga.delivery.accepted"] * 3 + ["saga.delivery.rejected"] * 4)

    async def create_order(self):
        from app.routers import main_router
        from app.sql import schemas
        async with database.SessionLocal() as db:
            await db.execute(insert(models.KnownClient).values(id=1).on_conflict_do_nothing())
            await db.commit()
            order = await main_router.create_order(schemas.OrderPost(
                client_id=1, number_of_pieces=2, address="Madrid"), db, token_payload={"roles": ["admin"]})
            return order.id

    @unittest.skipUnless(SERVICE == "payment", "payment decision")
    async def test_saga_confirmation_charges_once_without_deducting_balance_again(self):
        from app.sql import crud
        async with database.SessionLocal() as db:
            await crud.deposit(db, 1, 100)
        command = {"saga_id": 1, "order_id": 1, "client_id": 1, "amount": 20}
        await self.send_saga("saga.payment.reserve", command)
        await self.send_saga("saga.payment.confirm", command)
        await self.send_saga("saga.payment.confirm", command, "retry")
        await self.send_saga("saga.payment.reserve", command, "late")
        async with database.SessionLocal() as db:
            self.assertEqual((await crud.get_account_by_client_id(db, 1)).balance, 80)
            self.assertEqual((await db.get(models.PaymentReservation, 1)).state, "Confirmed")
            self.assertEqual(await db.scalar(select(func.count()).select_from(models.Transaction).where(
                models.Transaction.type == "Charge")), 1)
        self.assertEqual(await self.facts(), ["saga.payment.reserved", "saga.payment.confirmed"])

    @unittest.skipUnless(SERVICE == "order", "saga successful coordination")
    async def test_saga_waits_for_both_acceptances_and_confirmed_payment(self):
        from app import workflow
        for delivery_first in (True, False):
            order_id = await self.create_order()
            async with database.SessionLocal() as db:
                saga = await db.scalar(select(models.OrderSaga).where(models.OrderSaga.order_id == order_id))
                saga_id = saga.id
            common = {"saga_id": saga_id, "order_id": order_id, "client_id": 1, "accepted": True}
            payment = {**common, "amount": 20}
            delivery = {**common, "delivery_id": order_id}
            responses = [("saga.payment.reserved", payment), ("saga.delivery.accepted", delivery)]
            if delivery_first:
                responses.reverse()
            await self.send_saga(*responses[0], suffix=str(order_id))
            async with database.SessionLocal() as db:
                self.assertFalse((await db.get(models.Order, order_id)).payment_completed)
                self.assertNotEqual((await db.get(models.OrderSaga, saga_id)).state, "PendingConfirmation")
            await self.send_saga(*responses[1], suffix=str(order_id))
            async with database.SessionLocal() as db:
                self.assertFalse((await db.get(models.Order, order_id)).payment_completed)
                self.assertEqual((await db.get(models.OrderSaga, saga_id)).state, "PendingConfirmation")
            await workflow.bootstrap()
            await self.send_saga("saga.payment.confirmed", payment, str(order_id))
            await self.send_saga("saga.payment.confirmed", payment, f"retry-{order_id}")
            await self.send_saga("saga.payment.reserved", payment, f"late-{order_id}")
            await workflow.bootstrap()
            async with database.SessionLocal() as db:
                self.assertEqual((await db.get(models.OrderSaga, saga_id)).state, "Approved")
                order = await db.get(models.Order, order_id)
                self.assertTrue(order.payment_completed)
                self.assertEqual(order.status, "Manufacturing")
                self.assertEqual(order.delivery_id, order_id)
        facts = await self.facts()
        self.assertEqual(facts.count("saga.payment.confirm"), 2)
        self.assertEqual(facts.count("order.paid"), 2)

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
