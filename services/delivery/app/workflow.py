"""Publish completed deliveries, retaining pending events across outages."""
from .events import record_event
from fastapi import HTTPException
from .sql import crud


async def notify_order(db, delivery):
    if delivery.workflow_managed and delivery.status == "Delivered":
        await record_event(db, "delivery.delivered", {
            "delivery_id": delivery.id, "order_id": delivery.order_id, "client_id": delivery.client_id,
        }, f"delivery.delivered:{delivery.id}")


async def set_delivery_status(db, delivery_id, update):
    delivery = await crud.get_delivery(db, delivery_id)
    if delivery is None:
        raise HTTPException(404, "Delivery not found")
    if delivery.status in ("Provisional", "Suspended", "Canceled"):
        raise HTTPException(409, "Delivery is awaiting saga approval")

    stages = {"Pending": 0, "Ready": 1, "Sent": 2, "Delivered": 3}
    current = stages.get(delivery.status, 0)
    target = stages[update.status]

    if target < current:
        return delivery

    if current == 0 and target > 1:
        raise HTTPException(409, "Manufacturing has not finished")
    if delivery.workflow_managed and current == 0 and target == 1:
        raise HTTPException(409, "Delivery readiness is set by manufacturing events")

    delivery.status = update.status
    if update.tracking_number is not None:
        delivery.tracking_number = update.tracking_number

    await notify_order(db, delivery)
    await db.commit()
    await db.refresh(delivery)
    return delivery
