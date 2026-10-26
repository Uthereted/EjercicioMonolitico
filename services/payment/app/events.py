"""Bind shared infrastructure to this service's own database and configuration."""
import os
from my_chassis.events import EventBus, RetryEvent
from .sql import database

bus = EventBus(database.Base, database.SessionLocal, service_name="payment-service",
               broker_url=os.getenv("RABBITMQ_URL", "amqp://manufacturing:manufacturing@localhost:5672/"),
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
