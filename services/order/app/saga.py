"""Coordinate order approval and compensate rejected payment or delivery decisions."""
from .contracts import SagaPaymentResult, SagaDeliveryResult
from .events import record_event, RetryEvent
from .sql import models

TOPICS = ("saga.payment.reserved", "saga.payment.rejected", "saga.delivery.accepted",
          "saga.delivery.rejected", "saga.payment.confirmed", "saga.payment.released",
          "saga.delivery.canceled")


async def start(db, order):
    saga = models.OrderSaga(order_id=order.id)
    db.add(saga)
    await db.flush()
    await stage_requests(db, order, saga)
    return saga


async def stage_requests(db, order, saga):
    payload = {"saga_id": saga.id, "order_id": order.id, "client_id": order.client_id}
    if saga.state in ("AwaitingCompensation", "PendingPaymentRelease", "PendingDeliveryCancellation"):
        await record_event(db, "saga.payment.release", {
            **payload, "amount": order.total_price,
        }, f"saga.payment.release:{saga.id}")
        await record_event(db, "saga.delivery.cancel", {
            **payload, "address": order.address,
        }, f"saga.delivery.cancel:{saga.id}")
        return
    if saga.state == "PendingConfirmation":
        await record_event(db, "saga.payment.confirm", {
            **payload, "amount": order.total_price,
        }, f"saga.payment.confirm:{saga.id}")
        return
    if saga.state not in ("PendingPaymentAndDelivery", "PendingPayment", "PendingDelivery"):
        return
    await record_event(db, "saga.payment.reserve", {
        **payload, "amount": order.total_price,
    }, f"saga.payment.reserve:{saga.id}")
    await record_event(db, "saga.delivery.create", {
        **payload, "address": order.address,
    }, f"saga.delivery.create:{saga.id}")


async def handle_event(db, event_type, payload):
    if event_type not in TOPICS:
        raise ValueError("Unsupported order saga response")
    payment = event_type.startswith("saga.payment.")
    result = (SagaPaymentResult if payment else SagaDeliveryResult).model_validate(payload)
    saga = await db.get(models.OrderSaga, result.saga_id)
    if saga is None or saga.order_id != result.order_id:
        raise ValueError("Unknown order saga")
    order = await db.get(models.Order, saga.order_id)
    if order.client_id != result.client_id or payment and order.total_price != result.amount:
        raise ValueError("Saga response does not match order")
    if result.accepted != (not event_type.endswith("rejected")):
        raise ValueError("Saga outcome does not match response")
    if not payment and order.delivery_id not in (None, result.delivery_id):
        raise ValueError("Conflicting saga delivery")
    if saga.state in ("Approved", "Suspended"):
        return
    compensating = saga.state in ("AwaitingCompensation", "PendingPaymentRelease", "PendingDeliveryCancellation")
    if event_type in ("saga.payment.released", "saga.delivery.canceled"):
        if not compensating:
            raise RetryEvent("Saga compensation has not started")
        if not payment:
            order.delivery_id = result.delivery_id
        if event_type == "saga.payment.released":
            if saga.state == "PendingPaymentRelease":
                saga.state = "Suspended"
            elif saga.state == "AwaitingCompensation":
                saga.state = "PendingDeliveryCancellation"
        else:
            if saga.state == "PendingDeliveryCancellation":
                saga.state = "Suspended"
            elif saga.state == "AwaitingCompensation":
                saga.state = "PendingPaymentRelease"
        if saga.state == "Suspended":
            order.status = "Suspended"
        return
    if compensating:
        return
    if not result.accepted:
        saga.state = "AwaitingCompensation"
        order.status = "Compensating"
        order.integration_error = result.reason
        await stage_requests(db, order, saga)
        return
    if event_type == "saga.payment.confirmed":
        if saga.state != "PendingConfirmation":
            raise RetryEvent("Saga is not ready for payment confirmation")
        saga.state = "Approved"
        order.payment_completed = True
        order.status = "Accepted"
        await db.flush()
        from .workflow import stage_order_facts
        await stage_order_facts(db, order)
        return
    if payment:
        if saga.state == "PendingPaymentAndDelivery":
            saga.state = "PendingDelivery"
        elif saga.state == "PendingPayment":
            saga.state = "PendingConfirmation"
    else:
        order.delivery_id = result.delivery_id
        if saga.state == "PendingPaymentAndDelivery":
            saga.state = "PendingPayment"
        elif saga.state == "PendingDelivery":
            saga.state = "PendingConfirmation"
    await stage_requests(db, order, saga)
