"""Delivery database operations."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from . import models


async def create_delivery_from_schema(db, delivery):
    existing = await db.scalar(select(models.Delivery).where(
        models.Delivery.order_id == delivery.order_id,
        models.Delivery.workflow_managed.is_(True),
    ))
    if existing is not None:
        if existing.client_id != delivery.client_id or existing.address != delivery.address:
            raise HTTPException(409, "Order already has a delivery with different details")
        return existing
    db_delivery = models.Delivery(**delivery.model_dump())
    db.add(db_delivery)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return await create_delivery_from_schema(db, delivery)
    await db.refresh(db_delivery)
    return db_delivery


async def get_delivery_list(db):
    return list((await db.scalars(select(models.Delivery))).all())


async def get_delivery(db, delivery_id):
    return await db.get(models.Delivery, delivery_id)


async def delete_delivery(db, delivery_id):
    delivery = await get_delivery(db, delivery_id)
    if delivery is not None:
        await db.delete(delivery)
        await db.commit()
    return delivery
