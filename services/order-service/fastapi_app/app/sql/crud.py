# -*- coding: utf-8 -*-
"""Functions that interact with the database."""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from . import models

logger = logging.getLogger(__name__)


# order functions ##################################################################################
async def create_order_from_schema(db: AsyncSession, order):
    """Persist a new order into the database."""
    db_order = models.Order(
        number_of_pieces=order.number_of_pieces,
        description=order.description
    )
    db.add(db_order)
    await db.commit()
    await db.refresh(db_order)
    return db_order


async def get_order_list(db: AsyncSession):
    """Load all the orders from the database."""
    return await get_list(db, models.Order)


async def get_order(db: AsyncSession, order_id):
    return await get_element_by_id(db, models.Order, order_id)


async def delete_order(db: AsyncSession, order_id):
    """Delete order from the database."""
    return await delete_element_by_id(db, models.Order, order_id)


async def update_order_status(db: AsyncSession, order_id, status):
    """Persist new order status on the database."""
    db_order = await get_element_by_id(db, models.Order, order_id)
    if db_order is not None:
        db_order.status = status
        await db.commit()
        await db.refresh(db_order)
    return db_order

# Generic functions ################################################################################
# READ
async def get_list(db: AsyncSession, model):
    """Retrieve a list of elements from database"""
    result = await db.execute(select(model))
    item_list = result.unique().scalars().all()
    return item_list


async def get_list_statement_result(db: AsyncSession, stmt):
    """Execute given statement and return list of items."""
    result = await db.execute(stmt)
    item_list = result.unique().scalars().all()
    return item_list


async def get_element_statement_result(db: AsyncSession, stmt):
    """Execute statement and return a single items"""
    result = await db.execute(stmt)
    item = result.scalar()
    return item


async def get_element_by_id(db: AsyncSession, model, element_id):
    """Retrieve any DB element by id."""
    if element_id is None:
        return None

    element = await db.get(model, element_id)
    return element


# DELETE
async def delete_element_by_id(db: AsyncSession, model, element_id):
    """Delete any DB element by id."""
    element = await get_element_by_id(db, model, element_id)
    if element is not None:
        await db.delete(element)
        await db.commit()
    return element
