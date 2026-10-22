"""Run independently: python tests/test_order_messaging.py."""
import os
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["SQLALCHEMY_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/order"))

from sqlalchemy import select
from fastapi import HTTPException
from app import workflow, messaging
from app.routers import main_router
from app.sql import database, models, schemas


class OrderMessagingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with database.engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.drop_all)
            await conn.run_sync(models.Base.metadata.create_all)

    async def asyncTearDown(self):
        await database.engine.dispose()

    async def create_order(self, db):
        with patch.object(workflow, "validate_client", new=AsyncMock()):
            order = await main_router.create_order(schemas.OrderPost(
                client_id=1, number_of_pieces=2, address="Madrid",
            ), db)
        return order

    async def test_creation_waits_for_publication_and_payment(self):
        async with database.SessionLocal() as db:
            order = await self.create_order(db)
            self.assertFalse(order.order_created_published)
            self.assertEqual(order.status, "PendingPayment")
            with patch.object(workflow.httpx, "AsyncClient") as client:
                await workflow.resume_order(db, order)
                client.assert_not_called()

    async def test_failed_commit_does_not_leave_order_to_publish(self):
        async with database.SessionLocal() as db:
            with patch.object(db, "commit", new=AsyncMock(side_effect=RuntimeError("failed"))):
                with self.assertRaises(RuntimeError):
                    await self.create_order(db)
            await db.rollback()
            self.assertIsNone(await db.scalar(select(models.Order)))

    async def test_payment_callback_and_duplicate_preserve_progress(self):
        async with database.SessionLocal() as db:
            order = await self.create_order(db)
            result = schemas.PaymentResult(client_id=1, amount=20, accepted=True)
            with patch.object(workflow, "resume_order", new=AsyncMock()):
                await main_router.payment_result(order.id, result, db)
                order.status = "Manufacturing"
                await db.commit()
                await main_router.payment_result(order.id, result, db)
            self.assertTrue(order.payment_completed)
            self.assertEqual(order.status, "Manufacturing")

    async def test_payment_event_and_duplicate_preserve_progress(self):
        async with database.SessionLocal() as db:
            order = await self.create_order(db)
            event = messaging.PaymentResultEvent(
                order_id=order.id, client_id=1, amount=20, accepted=True,
            )
        with patch.object(workflow, "resume_order", new=AsyncMock()) as resume:
            await messaging.handle_payment_result(event)
            async with database.SessionLocal() as db:
                order = await db.get(models.Order, event.order_id)
                self.assertTrue(order.payment_completed)
                self.assertEqual(order.status, "Accepted")
                order.status = "Manufacturing"
                await db.commit()
            await messaging.handle_payment_result(event)
            self.assertEqual(resume.await_count, 2)
        async with database.SessionLocal() as db:
            order = await db.get(models.Order, event.order_id)
            self.assertEqual(order.status, "Manufacturing")

    async def test_rejected_payment_event_does_not_start_manufacturing(self):
        async with database.SessionLocal() as db:
            order = await self.create_order(db)
            event = messaging.PaymentResultEvent(
                order_id=order.id, client_id=1, amount=20, accepted=False, reason="Insufficient funds",
            )
        with patch.object(workflow, "resume_order", new=AsyncMock()) as resume:
            await messaging.handle_payment_result(event)
            await messaging.handle_payment_result(event)
            resume.assert_not_awaited()
        async with database.SessionLocal() as db:
            order = await db.get(models.Order, event.order_id)
            self.assertFalse(order.payment_completed)
            self.assertEqual(order.status, "Rejected")
            self.assertEqual(order.integration_error, "Insufficient funds")
        event.accepted = True
        with self.assertRaises(ValueError):
            await messaging.handle_payment_result(event)

    async def test_payment_event_rejects_wrong_amount_or_client(self):
        async with database.SessionLocal() as db:
            order = await self.create_order(db)
        for client_id, amount in ((2, 20), (1, 30)):
            with self.assertRaises(ValueError):
                await messaging.handle_payment_result(messaging.PaymentResultEvent(
                    order_id=order.id, client_id=client_id, amount=amount, accepted=True,
                ))
        async with database.SessionLocal() as db:
            order = await db.get(models.Order, order.id)
            self.assertFalse(order.payment_completed)
            self.assertEqual(order.status, "PendingPayment")

    async def check_publication(self, failure):
        async with database.SessionLocal() as db:
            await self.create_order(db)
        with patch.object(messaging.aio_pika, "connect_robust", new=AsyncMock()) as connect:
            connection = connect.return_value
            channel = connection.channel.return_value
            exchange = channel.declare_exchange.return_value
            if failure:
                exchange.publish.side_effect = RuntimeError("broker unavailable")
            with patch.object(messaging.asyncio, "sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
                with self.assertRaises(asyncio.CancelledError):
                    await messaging.publish_pending_events()
            channel.declare_queue.assert_awaited_once_with("payment.events", durable=True)
            channel.declare_exchange.assert_awaited_once_with(
                "manufacturing.events", messaging.aio_pika.ExchangeType.TOPIC, durable=True)
            exchange.publish.assert_awaited_once()
            import json
            message = exchange.publish.call_args.args[0]
            self.assertEqual(json.loads(message.body), {"order_id": 1, "client_id": 1, "amount": 20})
            self.assertEqual(exchange.publish.call_args.kwargs["routing_key"], "order.created")
        async with database.SessionLocal() as db:
            order = await db.scalar(select(models.Order))
            self.assertEqual(order.order_created_published, not failure)

    async def test_confirmed_publication_marks_event(self):
        await self.check_publication(False)

    async def test_failed_publication_keeps_event_pending(self):
        await self.check_publication(True)

    async def prepare_delivery(self, db, finished=True):
        order = await self.create_order(db)
        order.payment_completed = True
        order.delivery_id = 5
        order.status = "ReadyForDelivery" if finished else "Manufacturing"
        for piece in await workflow.pieces_for_order(db, order.id):
            piece.status = "Manufactured" if finished else "Manufacturing"
        await db.commit()
        return messaging.DeliveryStatusUpdate(
            order_id=order.id, delivery_id=5, status="Delivered",
        )

    async def test_delivery_event_and_duplicate_complete_order(self):
        async with database.SessionLocal() as db:
            event = await self.prepare_delivery(db)
        await messaging.handle_delivery_update(event)
        await messaging.handle_delivery_update(event)
        async with database.SessionLocal() as db:
            order = await db.get(models.Order, event.order_id)
            self.assertEqual(order.status, "Delivered")

    async def test_delivery_event_preserves_manufacturing_checks(self):
        async with database.SessionLocal() as db:
            event = await self.prepare_delivery(db, finished=False)
        with self.assertRaises(HTTPException) as error:
            await messaging.handle_delivery_update(event)
        self.assertEqual(error.exception.status_code, 409)
        async with database.SessionLocal() as db:
            order = await db.get(models.Order, event.order_id)
            self.assertEqual(order.status, "Manufacturing")

    async def test_delivery_event_rejects_wrong_delivery(self):
        async with database.SessionLocal() as db:
            event = await self.prepare_delivery(db)
        event.delivery_id = 6
        with self.assertRaises(ValueError):
            await messaging.handle_delivery_update(event)


    async def test_manufacturing_request_is_confirmed_and_not_repeated(self):
        async with database.SessionLocal() as db:
            order = await self.create_order(db)
            order.payment_completed = True
            order.delivery_id = 5
            await db.commit()
            with patch.object(messaging, "publish_event", new=AsyncMock()) as publish:
                await workflow.resume_order(db, order)
                publish.assert_awaited_once_with(
                    "machine.pieces.requested", "machine.events",
                    {"order_id": order.id, "piece_ids": [1, 2]},
                    message_id=f"machine.pieces.requested:{order.id}",
                )
                self.assertTrue(order.manufacturing_requested)
                await workflow.resume_order(db, order)
                self.assertEqual(publish.await_count, 1)

    async def test_failed_manufacturing_publication_is_retried(self):
        async with database.SessionLocal() as db:
            order = await self.create_order(db)
            order.payment_completed = True
            order.delivery_id = 5
            await db.commit()
            with patch.object(messaging, "publish_event", new=AsyncMock(side_effect=RuntimeError("offline"))) as publish:
                await workflow.resume_order(db, order)
                self.assertFalse(order.manufacturing_requested)
                self.assertEqual(order.status, "Pending")
                publish.side_effect = None
                await workflow.resume_order(db, order)
                self.assertTrue(order.manufacturing_requested)
                self.assertEqual(order.status, "Manufacturing")

    async def test_piece_events_finish_order_and_ignore_duplicates(self):
        async with database.SessionLocal() as db:
            order = await self.create_order(db)
            order.payment_completed = True
            order.manufacturing_requested = True
            order.delivery_id = 5
            await db.commit()
        with patch.object(workflow.httpx, "AsyncClient") as client:
            client.return_value.__aenter__.return_value.patch.return_value = MagicMock()
            for piece_id in (1, 2):
                for status in ("Manufacturing", "Manufactured", "Manufactured", "Manufacturing"):
                    await messaging.handle_piece_update(messaging.PieceStatusUpdate(
                        piece_id=piece_id, status=status,
                    ))
        async with database.SessionLocal() as db:
            order = await db.get(models.Order, order.id)
            self.assertEqual(order.status, "ReadyForDelivery")
            self.assertTrue(all(p.status == "Manufactured" for p in await workflow.pieces_for_order(db, order.id)))

    async def test_piece_events_do_not_manufacture_unpaid_order(self):
        async with database.SessionLocal() as db:
            await self.create_order(db)
        with self.assertRaises(HTTPException):
            await messaging.handle_piece_update(messaging.PieceStatusUpdate(piece_id=1, status="Manufacturing"))
        async with database.SessionLocal() as db:
            self.assertEqual((await db.get(models.Piece, 1)).status, "Queued")

    async def test_shared_queue_dispatch_and_acknowledgement(self):
        for routing_key, payload, handler in (
            ("piece.status.update", b'{"piece_id":1,"status":"Manufacturing"}', "handle_piece_update"),
            ("delivery.status.update", b'{"order_id":1,"delivery_id":5,"status":"Delivered"}', "handle_delivery_update"),
        ):
            message = AsyncMock(body=payload, routing_key=routing_key)
            with patch.object(messaging, handler, new=AsyncMock()) as handle:
                await messaging.process_order_event(message)
                handle.assert_awaited_once()
                message.ack.assert_awaited_once()
                message.reject.assert_not_awaited()

    async def test_invalid_piece_event_is_rejected(self):
        message = AsyncMock(body=b'{"piece_id":0,"status":"Unknown"}', routing_key="piece.status.update")
        await messaging.process_order_event(message)
        message.reject.assert_awaited_once_with(requeue=False)
        message.ack.assert_not_awaited()

    async def test_piece_processing_failure_is_requeued(self):
        message = AsyncMock(body=b'{"piece_id":1,"status":"Manufactured"}', routing_key="piece.status.update")
        with patch.object(messaging, "handle_piece_update", new=AsyncMock(side_effect=RuntimeError("db offline"))), patch.object(messaging.asyncio, "sleep", new=AsyncMock()):
            await messaging.process_order_event(message)
        message.nack.assert_awaited_once_with(requeue=True)
        message.ack.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
