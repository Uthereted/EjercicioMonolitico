"""Manual unit tests for persisted Saga reply destinations; no broker needed."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from my_chassis.messaging import ExchangeRouter


class SagaRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.domain = SimpleNamespace(publish=AsyncMock())
        self.commands = SimpleNamespace(publish=AsyncMock())
        self.responses = SimpleNamespace(publish=AsyncMock())
        self.other_responses = SimpleNamespace(publish=AsyncMock())
        self.router = ExchangeRouter(
            self.domain, self.responses,
            response_exchanges={"responses": self.responses, "other": self.other_responses},
        )

    def message(self, **payload):
        return SimpleNamespace(body=json.dumps({"data": payload}).encode())

    async def test_reply_uses_destination_from_persisted_message(self):
        message = self.message(response_exchange="other")
        await self.router.publish(message, routing_key="saga.payment.reserved", mandatory=True, timeout=5)
        self.other_responses.publish.assert_awaited_once_with(
            message, routing_key="saga.payment.reserved", mandatory=True, timeout=5,
        )
        self.responses.publish.assert_not_awaited()
        self.domain.publish.assert_not_awaited()

    async def test_facts_stay_on_domain_exchange(self):
        message = self.message(response_exchange="other")
        await self.router.publish(message, routing_key="order.created")
        self.domain.publish.assert_awaited_once_with(message, routing_key="order.created")
        self.other_responses.publish.assert_not_awaited()

    async def test_command_stays_on_command_exchange(self):
        router = ExchangeRouter(self.domain, self.commands)
        message = self.message(response_exchange="responses")
        await router.publish(message, routing_key="saga.payment.reserve")
        self.commands.publish.assert_awaited_once_with(message, routing_key="saga.payment.reserve")
        self.responses.publish.assert_not_awaited()

    async def test_old_outbox_reply_keeps_original_destination(self):
        message = self.message(saga_id=1)
        await self.router.publish(message, routing_key="saga.delivery.accepted")
        self.responses.publish.assert_awaited_once_with(message, routing_key="saga.delivery.accepted")

    async def test_unknown_destinations_are_rejected(self):
        for value in ("unknown", "", None, ["responses"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.router.publish(self.message(response_exchange=value), routing_key="saga.payment.released")
        self.domain.publish.assert_not_awaited()
        self.responses.publish.assert_not_awaited()
        self.other_responses.publish.assert_not_awaited()

    async def test_publish_failure_propagates_to_outbox(self):
        self.other_responses.publish.side_effect = ConnectionError("Disconnected")
        with self.assertRaises(ConnectionError):
            await self.router.publish(self.message(response_exchange="other"), routing_key="saga.payment.confirmed")


if __name__ == "__main__":
    unittest.main()
