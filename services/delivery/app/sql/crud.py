"""Delivery database operations."""
from sqlalchemy import select
from . import models


async def get_delivery_list(db):
    return list((await db.scalars(select(models.Delivery))).all())


async def get_delivery(db, delivery_id):
    return await db.get(models.Delivery, delivery_id)
