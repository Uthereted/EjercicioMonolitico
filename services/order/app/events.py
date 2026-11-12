"""Bind shared infrastructure to this service's own database and configuration."""
import os
from my_chassis.events import RetryEvent
from service_logging import LoggedEventBus as EventBus
from my_chassis.messaging import TopicTransport
from .sql import database

BROKER_URL = os.getenv("RABBITMQ_URL", "amqp://manufacturing:manufacturing@localhost:5672/")
saga_outgoing = TopicTransport(BROKER_URL, "manufacturing.commands")
saga_incoming = TopicTransport(BROKER_URL, "manufacturing.saga.responses")


class ExchangeRouter:
    """Keep the existing outbox, routing saga messages to their own exchange."""

    def __init__(self, domain, saga):
        self.domain = domain
        self.saga = saga

    async def publish(self, message, *, routing_key, **kwargs):
        exchange = self.saga if routing_key.startswith("saga.") else self.domain
        return await exchange.publish(message, routing_key=routing_key, **kwargs)


class SagaEventBus(EventBus):
    async def provision(self, channel):
        return ExchangeRouter(await super().provision(channel), await saga_outgoing.provision(channel))

    async def subscribe(self, channel, queue_name, topics):
        if queue_name == "order.saga":
            return await saga_incoming.subscribe(channel, queue_name, topics)
        return await super().subscribe(channel, queue_name, topics)


bus = SagaEventBus(database.Base, database.SessionLocal, service_name="order-service",
               broker_url=BROKER_URL,
               exchange_name=os.getenv("RABBITMQ_EXCHANGE", "manufacturing.events"))
Outbox, Inbox, SigningKey = bus.Outbox, bus.Inbox, bus.SigningKey
record_event = bus.record_event
provision = bus.provision
subscribe = bus.subscribe
flush_outbox = bus.flush_outbox
publish_pending_events = bus.publish_pending_events
dispatch = bus.dispatch
process_event = bus.process_event
consume_events = bus.consume_events
