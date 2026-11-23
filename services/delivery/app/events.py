"""Bind shared infrastructure to this service's own database and configuration."""
import os
from my_chassis.events import RetryEvent
from service_logging import LoggedEventBus as EventBus
from my_chassis.messaging import TopicTransport, ExchangeRouter
from .sql import database

BROKER_URL = os.getenv("RABBITMQ_URL", "amqps://manufacturing:manufacturing@localhost:5671/")
saga_outgoing = TopicTransport(BROKER_URL, "manufacturing.saga.responses")
saga_incoming = TopicTransport(BROKER_URL, "manufacturing.commands")


class SagaEventBus(EventBus):
    async def provision(self, channel):
        domain = await super().provision(channel)
        responses = await saga_outgoing.provision(channel)
        return ExchangeRouter(domain, responses,
                              response_exchanges={saga_outgoing.exchange_name: responses})

    async def subscribe(self, channel, queue_name, topics):
        if queue_name == "delivery.saga":
            return await saga_incoming.subscribe(channel, queue_name, topics)
        return await super().subscribe(channel, queue_name, topics)


bus = SagaEventBus(database.Base, database.SessionLocal, service_name="delivery-service",
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
