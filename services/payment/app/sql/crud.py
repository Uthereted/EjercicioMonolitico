"""Account queries and deposits; order payments are handled by the saga."""
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from . import models


async def get_account_by_client_id(db, client_id):
    result = await db.execute(select(models.Account).where(models.Account.client_id == client_id))
    return result.unique().scalar_one_or_none()


async def deposit(db, client_id, amount):
    # Create the account only if needed, in the same transaction as the deposit.
    await db.execute(insert(models.Account).values(client_id=client_id, balance=0)
                     .on_conflict_do_nothing(index_elements=["client_id"]))
    account_id = await db.scalar(select(models.Account.id).where(models.Account.client_id == client_id))
    await db.execute(update(models.Account).where(models.Account.id == account_id).values(
        balance=models.Account.balance + amount
    ))
    db.add(models.Transaction(account_id=account_id, amount=amount, type="Deposit"))
    await db.commit()
    db.expire_all()
    return await get_account_by_client_id(db, client_id)
