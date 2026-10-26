"""The payment service decides how to react to an order.created fact."""
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from .contracts import ClientCreated, ClientCatalogue, OrderCreated
from .events import record_event
from .sql import models, crud


TOPICS = ('client.created', 'clients.catalogue.published', 'order.created', 'auth.signing-key.published')

async def handle_event(db, event_type, payload):
    if event_type in ("client.created", "clients.catalogue.published"):
        ids = ([ClientCreated.model_validate(payload).client_id] if event_type == "client.created"
               else ClientCatalogue.model_validate(payload).client_ids)
        for client_id in ids:
            await db.execute(insert(models.KnownClient).values(id=client_id).on_conflict_do_nothing())
        return
    if event_type != "order.created":
        raise ValueError("Unsupported payment fact")
    fact = OrderCreated.model_validate(payload)
    result = await db.get(models.OrderPaymentResult, fact.order_id)
    if result is None:
        await db.execute(insert(models.Account).values(client_id=fact.client_id, balance=0)
                         .on_conflict_do_nothing(index_elements=["client_id"]))
        account = await crud.get_account_by_client_id(db, fact.client_id)
        previous = await db.scalar(select(models.Transaction).where(
            models.Transaction.type == "Charge", models.Transaction.order_id == fact.order_id))
        if previous is not None:
            if previous.account_id != account.id or previous.amount != fact.amount:
                raise ValueError("Conflicting existing charge")
            accepted = True
        else:
            changed = await db.execute(update(models.Account).where(
                models.Account.id == account.id, models.Account.balance >= fact.amount
            ).values(balance=models.Account.balance - fact.amount))
            accepted = changed.rowcount == 1
            if accepted:
                db.add(models.Transaction(account_id=account.id, amount=fact.amount,
                                          type="Charge", order_id=fact.order_id))
        result = models.OrderPaymentResult(order_id=fact.order_id, client_id=fact.client_id,
                                           amount=fact.amount, accepted=accepted,
                                           reason="" if accepted else "Client has insufficient funds")
        db.add(result)
        await db.flush()
    if result.client_id != fact.client_id or result.amount != fact.amount:
        raise ValueError("Conflicting order payment")
    event_type = "payment.approved" if result.accepted else "payment.rejected"
    await record_event(db, event_type, {
        "order_id": result.order_id, "client_id": result.client_id, "amount": result.amount,
        "accepted": result.accepted, "reason": result.reason,
    }, f"{event_type}:{result.order_id}")
