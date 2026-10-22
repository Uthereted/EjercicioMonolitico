"""Run independently: python tests/test_machine_messaging.py."""
import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/machine"))

from pydantic import ValidationError
from app.async_machine import Machine
from app.messaging import PiecesRequested, Publisher, consume_manufacturing_requests


class MachineMessagingTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_pieces_are_not_requeued_by_recovery_or_redelivery(self):
        publisher = AsyncMock()
        machine = Machine("http://orders", publisher)
        await machine.add_pieces_to_queue([1, 1, 2])
        self.assertEqual(await machine.list_queued_pieces(), [1, 2])
        with patch.dict(os.environ, {"MANUFACTURING_SECONDS": "0"}):
            worker = asyncio.create_task(machine.manufacturing_coroutine())
            try:
                await asyncio.wait_for(machine.queue.join(), timeout=2)
                await machine.add_pieces_to_queue([1, 2])
                self.assertEqual(await machine.list_queued_pieces(), [])
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker
        self.assertEqual([call.args for call in publisher.publish_piece_status.await_args_list], [
            (1, "Manufacturing"), (1, "Manufactured"),
            (2, "Manufacturing"), (2, "Manufactured"),
        ])

    async def test_notification_retries_until_confirmed(self):
        publisher = AsyncMock()
        publisher.publish_piece_status.side_effect = [RuntimeError("broker offline"), None]
        machine = Machine("http://orders", publisher)
        with patch("app.async_machine.asyncio.sleep", new=AsyncMock()):
            await machine.notify(1, "Manufactured")
        self.assertEqual(publisher.publish_piece_status.await_count, 2)

    async def test_publisher_declares_binding_and_sends_persistent_event(self):
        with patch("app.messaging.aio_pika.connect_robust", new=AsyncMock()) as connect:
            channel = connect.return_value.channel.return_value
            exchange = channel.declare_exchange.return_value
            publisher = Publisher()
            await publisher.publish_piece_status(7, "Manufactured")
            channel.declare_queue.assert_awaited_once_with("order.events", durable=True)
            channel.declare_queue.return_value.bind.assert_awaited_once_with(exchange, routing_key="piece.status.update")
            message = exchange.publish.call_args.args[0]
            self.assertEqual(json.loads(message.body), {"piece_id": 7, "status": "Manufactured"})
            self.assertEqual(message.delivery_mode, 2)
            self.assertTrue(exchange.publish.call_args.kwargs["mandatory"])
            await publisher.close()

    async def test_request_rejects_invalid_piece_ids(self):
        for ids in ([], [0], [-1], [True], ["1"]):
            with self.assertRaises(ValidationError):
                PiecesRequested(order_id=1, piece_ids=ids)


    async def test_consumer_queues_requests_and_rejects_malformed_messages(self):
        invalid = AsyncMock(body=b'{"order_id":1,"piece_ids":[-1]}')
        valid = AsyncMock(body=b'{"order_id":1,"piece_ids":[7,8]}')

        async def stream():
            yield invalid
            yield valid
            raise asyncio.CancelledError

        machine = AsyncMock()
        with patch("app.messaging.aio_pika.connect_robust", new=AsyncMock()) as connect:
            channel = connect.return_value.channel.return_value
            queue = channel.declare_queue.return_value
            queue.iterator = MagicMock()
            queue.iterator.return_value.__aenter__.return_value = stream()
            with self.assertRaises(asyncio.CancelledError):
                await consume_manufacturing_requests(machine)
            queue.bind.assert_awaited_once_with(
                channel.declare_exchange.return_value, routing_key="machine.pieces.requested",
            )
        invalid.reject.assert_awaited_once_with(requeue=False)
        machine.add_pieces_to_queue.assert_awaited_once_with([7, 8])
        valid.ack.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
