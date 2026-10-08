"""Atomic balance changes and idempotent charges keyed by order id."""
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from . import models


class InsufficientFundsError(Exception):
    pass


class PaymentConflictError(Exception):
    pass


async def get_account_by_client_id(db, client_id):
    result = await db.execute(select(models.Account).where(models.Account.client_id == client_id))
    return result.unique().scalar_one_or_none()


async def get_or_create_account(db, client_id):
    account = await get_account_by_client_id(db, client_id)
    if account is None:
        db.add(models.Account(client_id=client_id, balance=0))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
        account = await get_account_by_client_id(db, client_id)
    return account


async def refreshed_account(db, client_id):
    db.expire_all()
    return await get_account_by_client_id(db, client_id)


async def deposit(db, client_id, amount):
    account = await get_or_create_account(db, client_id)
    await db.execute(update(models.Account).where(models.Account.id == account.id).values(
        balance=models.Account.balance + amount
    ))
    db.add(models.Transaction(account_id=account.id, amount=amount, type="Deposit"))
    await db.commit()
    return await refreshed_account(db, client_id)


async def charge(db, client_id, amount, order_id):
    # A retry after a lost response must return the original charge, not charge twice.
    existing = await db.scalar(select(models.Transaction).where(
        models.Transaction.type == "Charge", models.Transaction.order_id == order_id
    ))
    if existing is not None:
        account = await get_account_by_client_id(db, client_id)
        if account is None or existing.account_id != account.id or existing.amount != amount:
            raise PaymentConflictError("Order was already charged with different payment details")
        return account
    account = await get_or_create_account(db, client_id)
    result = await db.execute(update(models.Account).where(
        models.Account.id == account.id, models.Account.balance >= amount
    ).values(balance=models.Account.balance - amount))
    if result.rowcount == 0:
        await db.rollback()
        # Another concurrent request may have completed the same charge.
        existing = await db.scalar(select(models.Transaction).where(
            models.Transaction.type == "Charge", models.Transaction.order_id == order_id
        ))
        if existing is not None:
            return await charge(db, client_id, amount, order_id)
        raise InsufficientFundsError("Client has insufficient funds")
    db.add(models.Transaction(account_id=account.id, amount=amount,
                              type="Charge", order_id=order_id))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return await charge(db, client_id, amount, order_id)
    return await refreshed_account(db, client_id)
