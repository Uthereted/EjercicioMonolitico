"""Bind shared infrastructure to this service's own database and configuration."""
import os
from service_logging import LoggedEventBus as EventBus
from .sql import database

bus = EventBus(database.Base, database.SessionLocal, service_name="client-service",
               broker_url=os.getenv("RABBITMQ_URL", "amqps://manufacturing:manufacturing@localhost:5671/"),
               exchange_name=os.getenv("RABBITMQ_EXCHANGE", "manufacturing.events"))
Outbox, Inbox, SigningKey = bus.Outbox, bus.Inbox, bus.SigningKey
record_event = bus.record_event
publish_pending_events = bus.publish_pending_events
dispatch = bus.dispatch
consume_events = bus.consume_events
