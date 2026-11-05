"""Account queries and deposits; order payments are handled by the saga."""
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from . import models


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
