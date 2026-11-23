"""Order database operations."""
from sqlalchemy import select
from . import models


async def get_order_list(db, client_id=None):
    query = select(models.Order)
    if client_id is not None:
        query = query.where(models.Order.client_id == client_id)
    return list((await db.scalars(query)).all())


async def get_order(db, order_id):
    return await db.get(models.Order, order_id)
