"""Reserve available balance without recording a final charge."""
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from .contracts import ReservePayment
from .events import record_event, RetryEvent
from .sql import models

TOPICS = ("saga.payment.reserve", "saga.payment.confirm", "saga.payment.release")


async def handle_event(db, event_type, payload):
    if event_type == "saga.payment.release":
        await release(db, ReservePayment.model_validate(payload))
        return
    if event_type == "saga.payment.confirm":
        await confirm(db, ReservePayment.model_validate(payload))
        return
    if event_type != "saga.payment.reserve":
        raise ValueError("Unsupported payment saga command")
    command = ReservePayment.model_validate(payload)
    reservation = await db.get(models.PaymentReservation, command.saga_id)
    if reservation is None:
        await db.execute(insert(models.Account).values(client_id=command.client_id, balance=0)
                         .on_conflict_do_nothing(index_elements=["client_id"]))
        changed = await db.execute(update(models.Account).where(
            models.Account.client_id == command.client_id,
            models.Account.balance >= command.amount,
        ).values(balance=models.Account.balance - command.amount))
        accepted = changed.rowcount == 1
        reservation = models.PaymentReservation(
            **command.model_dump(), state="Reserved" if accepted else "Rejected",
            reason="" if accepted else "Client has insufficient funds",
        )
        db.add(reservation)
        await db.flush()
    if (reservation.order_id, reservation.client_id, reservation.amount) != (
        command.order_id, command.client_id, command.amount
    ):
        raise ValueError("Conflicting payment reservation")
    accepted = reservation.state in ("Reserved", "Confirmed")
    response = "saga.payment.reserved" if accepted else "saga.payment.rejected"
    await record_event(db, response, {
        **command.model_dump(), "accepted": accepted, "reason": reservation.reason,
    }, f"{response}:{command.saga_id}")


async def confirm(db, command):
    reservation = await db.get(models.PaymentReservation, command.saga_id)
    if reservation is None:
        raise RetryEvent("Payment reservation is pending")
    if (reservation.order_id, reservation.client_id, reservation.amount) != (
        command.order_id, command.client_id, command.amount
    ) or reservation.state not in ("Reserved", "Confirmed"):
        raise ValueError("Invalid payment reservation confirmation")
    if reservation.state == "Reserved":
        account_id = await db.scalar(select(models.Account.id).where(
            models.Account.client_id == command.client_id))
        db.add(models.Transaction(account_id=account_id, order_id=command.order_id,
                                  amount=command.amount, type="Charge"))
        reservation.state = "Confirmed"
        await db.flush()
    await record_event(db, "saga.payment.confirmed", {
        **command.model_dump(), "accepted": True, "reason": "",
    }, f"saga.payment.confirmed:{command.saga_id}")


async def release(db, command):
    reservation = await db.get(models.PaymentReservation, command.saga_id)
    if reservation is None:
        raise RetryEvent("Payment reservation decision is pending")
    if (reservation.order_id, reservation.client_id, reservation.amount) != (
        command.order_id, command.client_id, command.amount
    ) or reservation.state not in ("Reserved", "Rejected", "Released"):
        raise ValueError("Invalid payment reservation release")
    if reservation.state == "Reserved":
        changed = await db.execute(update(models.Account).where(
            models.Account.client_id == command.client_id,
        ).values(balance=models.Account.balance + reservation.amount))
        if changed.rowcount != 1:
            raise ValueError("Reservation account is missing")
    reservation.state = "Released"
    await record_event(db, "saga.payment.released", {
        **command.model_dump(), "accepted": True, "reason": "",
    }, f"saga.payment.released:{command.saga_id}")
