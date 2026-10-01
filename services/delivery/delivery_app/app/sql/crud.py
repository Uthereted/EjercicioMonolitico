from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from . import models



# Delivery functions ###############################################################################
async def create_delivery_from_schema(db: AsyncSession, delivery):
    """Persist a new delivery into the database."""
    db_delivery = models.Delivery(
        client_id=delivery.client_id,
        order_id=delivery.order_id,
        address=delivery.address,
        status=delivery.status,
        tracking_number=delivery.tracking_number,
    )
    db.add(db_delivery)
    await db.commit()
    await db.refresh(db_delivery)
    return db_delivery


async def get_delivery_list(db: AsyncSession):
    """Load all deliveries from the database."""
    return await get_list(db, models.Delivery)


async def get_delivery(db: AsyncSession, delivery_id):
    """Load a delivery from the database."""
    return await get_element_by_id(db, models.Delivery, delivery_id)


async def delete_delivery(db: AsyncSession, delivery_id):
    """Delete a delivery from the database."""
    return await delete_element_by_id(db, models.Delivery, delivery_id)


async def update_delivery_status(db: AsyncSession, delivery_id, status):
    """Persist a new delivery status in the database."""
    db_delivery = await get_element_by_id(
        db,
        models.Delivery,
        delivery_id,
    )

    if db_delivery is not None:
        db_delivery.status = status
        await db.commit()
        await db.refresh(db_delivery)

    return db_delivery


# Generic functions ################################################################################
# READ
async def get_list(db: AsyncSession, model):
    """Retrieve a list of elements from the database."""
    result = await db.execute(select(model))
    item_list = result.unique().scalars().all()
    return item_list


async def get_element_by_id(db: AsyncSession, model, element_id):
    """Retrieve any database element by ID."""
    if element_id is None:
        return None

    return await db.get(model, element_id)


# DELETE
async def delete_element_by_id(db: AsyncSession, model, element_id):
    """Delete any database element by ID."""
    element = await get_element_by_id(db, model, element_id)

    if element is not None:
        await db.delete(element)
        await db.commit()

    return element