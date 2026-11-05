"""Delivery database operations."""
from fastapi import HTTPException
from sqlalchemy import select
from . import models


async def get_delivery_list(db):
    return list((await db.scalars(select(models.Delivery))).all())


async def get_delivery(db, delivery_id):
    return await db.get(models.Delivery, delivery_id)


async def delete_delivery(db, delivery_id):
    delivery = await get_delivery(db, delivery_id)
    if delivery is not None:
        if delivery.workflow_managed:
            raise HTTPException(409, "Workflow deliveries are retained; cancellation is managed by the saga")
        await db.delete(delivery)
        await db.commit()
    return delivery
