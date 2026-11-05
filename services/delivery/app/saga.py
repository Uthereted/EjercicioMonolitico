"""Create a provisional delivery and retain its postal coverage decision."""
import re
from sqlalchemy import select
from .contracts import CreateSagaDelivery
from .events import record_event, RetryEvent
from .sql import models

TOPICS = ("saga.delivery.create", "saga.delivery.cancel")


async def handle_event(db, event_type, payload):
    if event_type == "saga.delivery.cancel":
        await cancel(db, CreateSagaDelivery.model_validate(payload))
        return
    if event_type != "saga.delivery.create":
        raise ValueError("Unsupported delivery saga command")
    command = CreateSagaDelivery.model_validate(payload)
    result = await db.get(models.DeliverySagaResult, command.saga_id)
    if result is None:
        postal_codes = re.findall(r"\b[0-9]{5}\b", command.address)
        accepted = len(postal_codes) == 1 and postal_codes[0][:2] in ("01", "20", "48")
        delivery = await db.scalar(select(models.Delivery).where(
            models.Delivery.order_id == command.order_id, models.Delivery.workflow_managed.is_(True)))
        if delivery is not None:
            raise ValueError("Order already has a delivery outside this saga")
        delivery = models.Delivery(order_id=command.order_id, client_id=command.client_id,
                                   address=command.address, workflow_managed=True,
                                   status="Provisional" if accepted else "Suspended")
        db.add(delivery)
        await db.flush()
        result = models.DeliverySagaResult(
            saga_id=command.saga_id, order_id=command.order_id, delivery_id=delivery.id,
            accepted=accepted, reason="" if accepted else "Address must include one postal code in 01, 20 or 48",
        )
        db.add(result)
        await db.flush()
    delivery = await db.get(models.Delivery, result.delivery_id)
    if delivery is None or (result.order_id, delivery.client_id, delivery.address) != (
        command.order_id, command.client_id, command.address
    ):
        raise ValueError("Conflicting saga delivery")
    response = "saga.delivery.accepted" if result.accepted else "saga.delivery.rejected"
    await record_event(db, response, {
        "saga_id": command.saga_id, "order_id": result.order_id,
        "client_id": delivery.client_id, "delivery_id": delivery.id,
        "accepted": result.accepted, "reason": result.reason,
    }, f"{response}:{command.saga_id}")


async def cancel(db, command):
    result = await db.get(models.DeliverySagaResult, command.saga_id)
    if result is None:
        raise RetryEvent("Provisional delivery creation is pending")
    delivery = await db.get(models.Delivery, result.delivery_id)
    if delivery is None or (result.order_id, delivery.client_id, delivery.address) != (
        command.order_id, command.client_id, command.address
    ) or delivery.status not in ("Provisional", "Suspended", "Canceled"):
        raise ValueError("Invalid provisional delivery cancellation")
    delivery.status = "Canceled"
    await record_event(db, "saga.delivery.canceled", {
        "saga_id": command.saga_id, "order_id": result.order_id,
        "client_id": delivery.client_id, "delivery_id": delivery.id,
        "accepted": True, "reason": "",
    }, f"saga.delivery.canceled:{command.saga_id}")
