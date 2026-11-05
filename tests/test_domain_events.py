"""Domain choreography tests. Run each service in a separate Python process."""
import argparse
import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
            await conn.run_sync(database.Base.metadata.create_all)

    async def asyncTearDown(self):
        await database.engine.dispose()

    async def send(self, event_type, payload, suffix="1"):
        await events.dispatch(f"{event_type}:{suffix}", event_type, payload, messaging.handle_event)

    async def facts(self):
        async with database.SessionLocal() as db:
            return list((await db.scalars(select(events.Outbox.event_type).order_by(events.Outbox.id))).all())

    async def send_saga(self, event_type, payload, suffix="1"):
        from app import saga
        await events.dispatch(f"{event_type}:{suffix}", event_type, payload, saga.handle_event)

    async def legacy_request(self, module, method, path, body=None):
        import httpx
        from fastapi import FastAPI
        api = FastAPI()
        api.include_router(module.router)
        api.dependency_overrides[module.verify_jwt] = lambda: {"roles": ["admin"]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
            return await client.request(method, path, json=body)

    @unittest.skipUnless(SERVICE == "order", "legacy order mutation guards")
    async def test_manual_callbacks_and_legacy_payment_events_cannot_override_saga(self):
        from app.routers import main_router
        order_id = await self.create_order()
        async with database.SessionLocal() as db:
            piece_id = await db.scalar(select(models.Piece.id).where(models.Piece.order_id == order_id))
        payment = {"client_id": 1, "amount": 20, "accepted": True}
        for path, body in ((f"/order/{order_id}/payment", payment),
                           (f"/order/{order_id}/status", {"status": "Finished"}),
                           (f"/piece/{piece_id}", {"status": "Manufacturing"})):
            response = await self.legacy_request(main_router, "PATCH", path, body)
            self.assertEqual(response.status_code, 409, response.text)
        with self.assertRaises(ValueError):
            await self.send("payment.approved", {**payment, "order_id": order_id})
        async with database.SessionLocal() as db:
            order = await db.get(models.Order, order_id)
            self.assertFalse(order.payment_completed)
            self.assertEqual(order.status, "PendingPayment")
            self.assertEqual((await db.get(models.Piece, piece_id)).status, "Queued")
            # Even an approved saga must finish delivery through its delivery events.
            (await db.scalar(select(models.OrderSaga))).state = "Approved"
            order.payment_completed = True
            order.delivery_id = 1
            order.status = "ReadyForDelivery"
            for piece in (await db.scalars(select(models.Piece))).all():
                piece.status = "Manufactured"
            await db.commit()
        response = await self.legacy_request(main_router, "PATCH", f"/order/{order_id}/status", {"status": "Delivered"})
        self.assertEqual(response.status_code, 409, response.text)
        async with database.SessionLocal() as db:
            self.assertEqual((await db.get(models.Order, order_id)).status, "ReadyForDelivery")

    @unittest.skipUnless(SERVICE == "payment", "legacy charge guard")
    async def test_http_charge_is_blocked_before_and_after_reservation(self):
        from app.routers import payment_router
        from app.sql import crud
        async with database.SessionLocal() as db:
            await crud.deposit(db, 1, 100)
        command = {"saga_id": 1, "order_id": 1, "client_id": 1, "amount": 20}
        for reserved in (False, True):
            if reserved:
                await self.send_saga("saga.payment.reserve", command)
            response = await self.legacy_request(payment_router, "POST", "/payment/1/charge", {"amount": 20, "order_id": 1})
            self.assertEqual(response.status_code, 409, response.text)
            async with database.SessionLocal() as db:
                self.assertEqual((await crud.get_account_by_client_id(db, 1)).balance, 80 if reserved else 100)
                self.assertEqual(await db.scalar(select(func.count()).select_from(models.Transaction).where(
                    models.Transaction.type == "Charge")), 0)

    @unittest.skipUnless(SERVICE == "delivery", "legacy delivery guards")
    async def test_delivery_routes_cannot_create_delete_or_advance_saga_work(self):
        from app.routers import delivery_router
        command = {"saga_id": 1, "order_id": 1, "client_id": 1, "address": "48001 Bilbao"}
        response = await self.legacy_request(delivery_router, "POST", "/deliveries/delivery", command)
        self.assertEqual(response.status_code, 409, response.text)
        async with database.SessionLocal() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(models.Delivery)), 0)
        await self.send_saga("saga.delivery.create", command)
        async with database.SessionLocal() as db:
            delivery_id = (await db.scalar(select(models.Delivery))).id
        response = await self.legacy_request(delivery_router, "DELETE", f"/deliveries/delivery/{delivery_id}")
        self.assertEqual(response.status_code, 409, response.text)
        await self.send("order.paid", {**PAID, "address": command["address"]})
        response = await self.legacy_request(delivery_router, "PATCH", f"/deliveries/delivery/{delivery_id}/status", {"status": "Ready"})
        self.assertEqual(response.status_code, 409, response.text)
        await self.send("order.manufactured", {**PAID, "address": command["address"], "manufactured_piece_ids": [1, 2]})
        response = await self.legacy_request(delivery_router, "PATCH", f"/deliveries/delivery/{delivery_id}/status", {"status": "Delivered"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "Delivered")

    @unittest.skipUnless(SERVICE == "machine", "legacy manufacturing guards")
    async def test_manual_queue_cannot_create_work_or_remove_approved_order_work(self):
        from app.async_machine import Machine
        from fastapi import HTTPException
        machine = Machine()
        with self.assertRaises(HTTPException) as caught:
            await machine.add_pieces_to_queue([1])
        self.assertEqual(caught.exception.status_code, 409)
        async with database.SessionLocal() as db:
            self.assertIsNone(await db.get(models.WorkPiece, 1))
        await self.send("order.paid", PAID)
        await machine.add_pieces_to_queue([1])
        with self.assertRaises(HTTPException) as caught:
            await machine.remove_piece_from_queue(1)
        self.assertEqual(caught.exception.status_code, 409)
        async with database.SessionLocal() as db:
            self.assertEqual((await db.get(models.WorkPiece, 1)).status, "Queued")

    @unittest.skipUnless(SERVICE in ("order", "payment", "delivery"), "separate saga exchanges")
    async def test_outbox_routes_domain_and_saga_messages_to_separate_exchanges(self):
        domain, saga_exchange = AsyncMock(), AsyncMock()
        saga_topic = "saga.payment.reserve" if SERVICE == "order" else f"saga.{SERVICE}.rejected"
        async with database.SessionLocal() as db:
            await events.record_event(db, "order.paid", {}, "domain-test")
            await events.record_event(db, saga_topic, {}, "saga-test")
            await db.commit()
        with patch.object(events.bus.transport, "provision", return_value=domain), \
                patch.object(events.saga_outgoing, "provision", return_value=saga_exchange):
            exchange = await events.provision(None)
        await events.flush_outbox(exchange)
        domain.publish.assert_awaited_once()
        saga_exchange.publish.assert_awaited_once()
        self.assertEqual(domain.publish.call_args.kwargs["routing_key"], "order.paid")
        self.assertEqual(saga_exchange.publish.call_args.kwargs["routing_key"], saga_topic)
        self.assertTrue(saga_exchange.publish.call_args.kwargs["mandatory"])
        self.assertEqual(json.loads(saga_exchange.publish.call_args.args[0].body)["event_id"], "saga-test")
        async with database.SessionLocal() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(events.Outbox).where(
                events.Outbox.published.is_(False))), 0)

    @unittest.skipUnless(SERVICE in ("order", "payment", "delivery"), "saga exchange publication recovery")
    async def test_failed_saga_exchange_publication_keeps_outbox_pending(self):
        async with database.SessionLocal() as db:
            await events.record_event(db, "saga.test", {}, "pending-test")
            await db.commit()
        domain, saga_exchange = AsyncMock(), AsyncMock()
        saga_exchange.publish.side_effect = RuntimeError("broker unavailable")
        exchange = events.ExchangeRouter(domain, saga_exchange)
        with self.assertRaises(RuntimeError):
            await events.flush_outbox(exchange)
        async with database.SessionLocal() as db:
            self.assertFalse(await db.scalar(select(events.Outbox.published)))
        saga_exchange.publish.side_effect = None
        await events.flush_outbox(exchange)
        async with database.SessionLocal() as db:
            self.assertTrue(await db.scalar(select(events.Outbox.published)))
        domain.publish.assert_not_awaited()

    @unittest.skipUnless(SERVICE in ("order", "payment", "delivery"), "saga exchange subscriptions")
    async def test_saga_queue_binds_only_to_its_incoming_exchange(self):
        from app import saga
        with patch.object(events.bus.transport, "subscribe") as domain, \
                patch.object(events.saga_incoming, "subscribe") as incoming:
            await events.subscribe(None, f"{SERVICE}.saga", saga.TOPICS)
            incoming.assert_awaited_once_with(None, f"{SERVICE}.saga", saga.TOPICS)
            domain.assert_not_awaited()
            await events.subscribe(None, f"{SERVICE}.domain", messaging.TOPICS)
            domain.assert_awaited_once_with(None, f"{SERVICE}.domain", messaging.TOPICS)
        definitions = json.loads((Path(__file__).resolve().parents[1] /
                                  "infrastructure/rabbitmq/domain-definitions.json").read_text())
        expected_in = "manufacturing.saga.responses" if SERVICE == "order" else "manufacturing.commands"
        expected_out = "manufacturing.commands" if SERVICE == "order" else "manufacturing.saga.responses"
        self.assertEqual(events.saga_incoming.exchange_name, expected_in)
        self.assertEqual(events.saga_outgoing.exchange_name, expected_out)
        for topic in saga.TOPICS:
            sources = [binding["source"] for binding in definitions["bindings"]
                       if binding["destination"] == f"{SERVICE}.saga" and binding["routing_key"] == topic]
            self.assertEqual(sources, [expected_in])

    @unittest.skipUnless(SERVICE == "client", "client address publication")
    async def test_client_address_is_required_persisted_and_published_in_snapshot(self):
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
                await messaging.publish_catalogue()
        async with database.SessionLocal() as db:
            created = json.loads(await db.scalar(select(events.Outbox.payload).where(
                events.Outbox.event_type == "client.created")))
            snapshot = json.loads(await db.scalar(select(events.Outbox.payload).where(
                events.Outbox.event_type == "clients.catalogue.published")))
            self.assertEqual(created, {"client_id": client.id, "address": "Calle Mayor 10", "postal_code": "01001"})
            self.assertEqual(snapshot, {"client_ids": [client.id], "clients": [created]})

    @unittest.skipUnless(SERVICE == "client", "existing client database")
    async def test_client_address_migration_preserves_legacy_clients(self):
        from sqlalchemy import text
        from app.sql import migrations, schemas
        async with database.engine.begin() as conn:
            await conn.execute(text("DROP TABLE clients"))
            await conn.execute(text("CREATE TABLE clients (id INTEGER PRIMARY KEY, name VARCHAR(256), email VARCHAR(256), creation_date DATETIME)"))
            await conn.execute(text("INSERT INTO clients (id, name, email) VALUES (1, 'Legacy', 'legacy@example.org')"))
            await conn.run_sync(migrations.upgrade)
            await conn.run_sync(migrations.upgrade)
        async with database.SessionLocal() as db:
            client = schemas.ClientResponse.model_validate(await db.get(models.Client, 1))
            self.assertEqual(client.name, "Legacy")
            self.assertIsNone(client.address)
            self.assertIsNone(client.postal_code)

    @unittest.skipUnless(SERVICE == "order", "client address projection")
    async def test_order_uses_client_address_from_snapshot_without_http(self):
        from app.routers import main_router
        from app.sql import schemas
        from fastapi import HTTPException
        await self.send("clients.catalogue.published", {
            "client_ids": [1], "clients": [{"client_id": 1, "address": "Calle Mayor 10", "postal_code": "01001"}]})
        await self.send("client.created", {"client_id": 1}, "legacy")
        await self.send("clients.catalogue.published", {"client_ids": [1, 2]}, "legacy")
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

    @unittest.skipUnless(SERVICE in ("payment", "delivery"), "compensation reordering")
    async def test_compensation_before_initial_command_retries(self):
        from app import saga
        command = {"saga_id": 1, "order_id": 1, "client_id": 1, "amount": 20, "address": "48001 Bilbao"}
        topic = "saga.payment.release" if SERVICE == "payment" else "saga.delivery.cancel"
        with self.assertRaises(events.RetryEvent):
            await self.send_saga(topic, command)
        first = "saga.payment.reserve" if SERVICE == "payment" else "saga.delivery.create"
        await self.send_saga(first, command)
        await self.send_saga(topic, command)
        self.assertEqual(len(await self.facts()), 2)

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

    @unittest.skipUnless(SERVICE == "payment", "saga reservation")
    async def test_reservation_holds_available_balance_once_without_charging(self):
        from app.sql import crud
        async with database.SessionLocal() as db:
            await crud.deposit(db, 1, 100)
        command = {"saga_id": 1, "order_id": 1, "client_id": 1, "amount": 60}
        await self.send_saga("saga.payment.reserve", command)
        await self.send_saga("saga.payment.reserve", command, "redelivery")
        await self.send_saga("saga.payment.reserve", {**command, "saga_id": 2, "order_id": 2}, "second")
        async with database.SessionLocal() as db:
            self.assertEqual((await crud.get_account_by_client_id(db, 1)).balance, 40)
            self.assertEqual((await db.get(models.PaymentReservation, 1)).state, "Reserved")
            self.assertEqual((await db.get(models.PaymentReservation, 2)).state, "Rejected")
            self.assertEqual(await db.scalar(select(func.count()).select_from(models.Transaction).where(
                models.Transaction.type == "Charge")), 0)
        self.assertEqual(await self.facts(), ["saga.payment.reserved", "saga.payment.rejected"])

    @unittest.skipUnless(SERVICE == "payment", "saga rejection")
    async def test_reservation_rejection_is_retained_after_deposit(self):
        from app.sql import crud
        command = {"saga_id": 1, "order_id": 1, "client_id": 1, "amount": 20}
        await self.send_saga("saga.payment.reserve", command)
        async with database.SessionLocal() as db:
            await crud.deposit(db, 1, 100)
        await self.send_saga("saga.payment.reserve", command, "retry")
        async with database.SessionLocal() as db:
            self.assertEqual((await crud.get_account_by_client_id(db, 1)).balance, 100)
        self.assertEqual(await self.facts(), ["saga.payment.rejected"])

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

    @unittest.skipUnless(SERVICE == "order", "order choreography")
    async def test_saga_creation_survives_restart_without_starting_legacy_flow(self):
        order_id = await self.create_order()
        from app import workflow
        await workflow.bootstrap()
        async with database.SessionLocal() as db:
            order = await db.get(models.Order, order_id)
            await workflow.resume_order(db, order)
            self.assertEqual(order.status, "PendingPayment")
            self.assertFalse(order.payment_completed)
            self.assertEqual(await db.scalar(select(func.count()).select_from(models.OrderSaga)), 1)
            saga = await db.scalar(select(models.OrderSaga))
            self.assertEqual(saga.order_id, order_id)
            self.assertEqual(saga.state, "PendingPaymentAndDelivery")
        self.assertEqual(await self.facts(), ["saga.payment.reserve", "saga.delivery.create"])

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

    @unittest.skipUnless(SERVICE == "order", "saga rejection guard")
    async def test_rejected_delivery_never_confirms_payment(self):
        order_id = await self.create_order()
        async with database.SessionLocal() as db:
            saga_id = (await db.scalar(select(models.OrderSaga))).id
        common = {"saga_id": saga_id, "order_id": order_id, "client_id": 1}
        await self.send_saga("saga.delivery.rejected", {
            **common, "delivery_id": 1, "accepted": False, "reason": "Outside coverage"})
        await self.send_saga("saga.payment.reserved", {**common, "amount": 20, "accepted": True})
        self.assertNotIn("saga.payment.confirm", await self.facts())
        self.assertNotIn("order.paid", await self.facts())

    @unittest.skipUnless(SERVICE == "delivery", "approved saga delivery")
    async def test_paid_order_activates_provisional_delivery_without_duplicates(self):
        address = "Calle Mayor 10, 48001 Bilbao"
        await self.send_saga("saga.delivery.create", {
            "saga_id": 1, "order_id": 1, "client_id": 1, "address": address})
        await self.send("order.paid", {**PAID, "address": address})
        await self.send("order.paid", {**PAID, "address": address}, "retry")
        async with database.SessionLocal() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(models.Delivery)), 1)
            self.assertEqual((await db.scalar(select(models.Delivery))).status, "Pending")

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
