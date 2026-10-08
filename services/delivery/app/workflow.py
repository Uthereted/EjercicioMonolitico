"""Notify orders when deliveries finish, retaining callbacks across outages."""
import asyncio
import logging
import os
import httpx
from sqlalchemy import inspect, select, text
from .sql import database, models

logger = logging.getLogger(__name__)
ORDERS_URL = os.getenv("ORDERS_SERVICE_URL", "http://localhost:13001").rstrip("/")
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
    if not delivery.workflow_managed or delivery.status != "Delivered" or delivery.order_notified:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.patch(f"{ORDERS_URL}/order/{delivery.order_id}/status",
                                          json={"status": "Delivered"})
            response.raise_for_status()
    except (httpx.RequestError, httpx.HTTPStatusError):
        logger.warning("Order notification pending for delivery %s", delivery.id)
        return
    delivery.order_notified = True
    await db.commit()
    await db.refresh(delivery)


async def retry_notifications():
    while True:
        await asyncio.sleep(RETRY_SECONDS)
        try:
            async with database.SessionLocal() as db:
                deliveries = list((await db.scalars(select(models.Delivery).where(
                    models.Delivery.status == "Delivered", models.Delivery.order_notified.is_(False),
                    models.Delivery.workflow_managed.is_(True),
                ))).all())
                for delivery in deliveries:
                    await notify_order(db, delivery)
        except Exception:
            logger.exception("Delivery callback retry failed; will retry later")
