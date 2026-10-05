# -*- coding: utf-8 -*-
"""Functions that interact with the database."""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from . import models

logger = logging.getLogger(__name__)


class InsufficientFundsError(Exception):
    """Raised when a charge is attempted without enough balance."""


# Account functions #################################################################################
async def get_account_by_client_id(db: AsyncSession, client_id):
    """Load an account from the database by client_id."""
    stmt = select(models.Account).where(models.Account.client_id == client_id)
    return await get_element_statement_result(db, stmt)


async def get_or_create_account(db: AsyncSession, client_id):
    """Return the account for a client, creating one (balance 0) if it doesn't exist yet."""
    account = await get_account_by_client_id(db, client_id)
    if account is None:
        account = models.Account(client_id=client_id, balance=0)
        db.add(account)
        await db.commit()
        await db.refresh(account)
    return account


# Transaction functions #############################################################################
async def deposit(db: AsyncSession, client_id: int, amount: int):
    """Adds funds to a client's account. Creates the account if it doesn't exist."""
    account = await get_or_create_account(db, client_id)

    account.balance += amount
    transaction = models.Transaction(
        account_id=account.id,
        amount=amount,
        type=models.Transaction.TYPE_DEPOSIT
    )
    db.add(transaction)
    await db.commit()
    await db.refresh(account)
    return account


async def charge(db: AsyncSession, client_id: int, amount: int, order_id: int):
    """
    Deducts funds from a client's account to pay for an order.

    Raises InsufficientFundsError if the account does not have enough balance.
    The Order service should treat that as a failed step in the order-creation
    flow and abort/compensate accordingly.
    """
    account = await get_or_create_account(db, client_id)

    if account.balance < amount:
        raise InsufficientFundsError(
            f"Client {client_id} has insufficient funds: "
            f"balance={account.balance}, requested={amount}"
        )

    account.balance -= amount
    transaction = models.Transaction(
        account_id=account.id,
        amount=amount,
        type=models.Transaction.TYPE_CHARGE,
        order_id=order_id
    )
    db.add(transaction)
    await db.commit()
    await db.refresh(account)
    return account


# Generic functions ################################################################################
async def get_element_statement_result(db: AsyncSession, stmt):
    """Execute statement and return a single item"""
    result = await db.execute(stmt)
    item = result.unique().scalar()
    return item