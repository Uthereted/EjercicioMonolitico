"""Delivery chooses its own actions from paid/manufactured order facts."""
from sqlalchemy import select
from .contracts import OrderPaid
from .events import record_event
from .sql import models


TOPICS = ('order.paid', 'order.manufactured', 'auth.signing-key.published')

async def handle_event(db, event_type, payload):
    if event_type not in ("order.paid", "order.manufactured"):
        raise ValueError("Unsupported delivery fact")
    fact = OrderPaid.model_validate(payload)
    delivery = await db.scalar(select(models.Delivery).where(
        models.Delivery.order_id == fact.order_id, models.Delivery.workflow_managed.is_(True)))
    if delivery is None:
        delivery = models.Delivery(order_id=fact.order_id, client_id=fact.client_id,
                                   address=fact.address, status="Pending", workflow_managed=True)
        db.add(delivery)
        await db.flush()
    if delivery.client_id != fact.client_id or delivery.address != fact.address:
        raise ValueError("Conflicting order delivery")
    data = {"delivery_id": delivery.id, "order_id": delivery.order_id, "client_id": delivery.client_id}
    await record_event(db, "delivery.created", data, f"delivery.created:{delivery.id}")
    if event_type == "order.manufactured" and delivery.status == "Pending":
        delivery.status = "Ready"
    # Also bridge existing deliveries during the transition to event choreography.
    if delivery.status in ("Ready", "Sent", "Delivered"):
        await record_event(db, "delivery.ready", data, f"delivery.ready:{delivery.id}")
    if delivery.status == "Delivered":
        await record_event(db, "delivery.delivered", data, f"delivery.delivered:{delivery.id}")
