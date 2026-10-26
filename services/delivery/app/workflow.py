"""Publish completed deliveries, retaining pending events across outages."""
import asyncio
import logging
import os
from .events import record_event
from fastapi import HTTPException
from sqlalchemy import inspect, select, text
from .sql import database, models, crud

logger = logging.getLogger(__name__)
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://manufacturing:manufacturing@localhost:5672/")
EXCHANGE = os.getenv("RABBITMQ_EXCHANGE", "manufacturing.events")
RETRY_SECONDS = float(os.getenv("REST_RETRY_SECONDS", "5"))


def upgrade(connection):
    columns = {column["name"] for column in inspect(connection).get_columns("delivery")}
    if "order_notified" not in columns:
        connection.execute(text("ALTER TABLE delivery ADD COLUMN order_notified BOOLEAN NOT NULL DEFAULT 0"))
    if "workflow_managed" not in columns:
        connection.execute(text("ALTER TABLE delivery ADD COLUMN workflow_managed BOOLEAN NOT NULL DEFAULT 0"))
    for index in models.Delivery.__table__.indexes:
        index.create(connection, checkfirst=True)


async def notify_order(db, delivery):
    if delivery.workflow_managed and delivery.status == "Delivered":
        await record_event(db, "delivery.delivered", {
            "delivery_id": delivery.id, "order_id": delivery.order_id, "client_id": delivery.client_id,
        }, f"delivery.delivered:{delivery.id}")


async def set_delivery_status(db, delivery_id, update):
    delivery = await crud.get_delivery(db, delivery_id)
    if delivery is None:
        raise HTTPException(404, "Delivery not found")

    stages = {"Pending": 0, "Ready": 1, "Sent": 2, "Delivered": 3}
    current = stages.get(delivery.status, 0)
    target = stages[update.status]

    if target < current:
        return delivery

    if current == 0 and target > 1:
        raise HTTPException(409, "Manufacturing has not finished")

    delivery.status = update.status
    if update.tracking_number is not None:
        delivery.tracking_number = update.tracking_number

    await notify_order(db, delivery)
    await db.commit()
    await db.refresh(delivery)
    return delivery
