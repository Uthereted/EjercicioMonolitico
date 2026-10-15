"""Order database operations."""
from sqlalchemy import select
from . import models


async def get_order_list(db):
    return list((await db.scalars(select(models.Order))).all())


async def get_order(db, order_id):
    return await db.get(models.Order, order_id)


async def delete_order(db, order_id):
    order = await get_order(db, order_id)
    if order is not None:
        await db.delete(order)
        await db.commit()
    return order
