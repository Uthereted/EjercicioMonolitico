from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from .. import dependencies
from ..sql import crud, schemas
from .. import workflow

router = APIRouter(
    prefix="/deliveries",
    tags=["Delivery"],
)

@router.get(
    "/",
    summary="Check whether the delivery service is running",
)
async def health_check():
    return {"detail": "OK"}

@router.post(
    "/delivery",
    response_model=schemas.Delivery,
    summary="Create a delivery",
    status_code=status.HTTP_201_CREATED,
)
async def create_delivery(
    delivery_schema: schemas.DeliveryPost,
    db: AsyncSession = Depends(dependencies.get_db),
):
    """Create a delivery."""
    return await crud.create_delivery_from_schema(db, delivery_schema)


@router.get(
    "/delivery",
    response_model=List[schemas.Delivery],
    summary="Retrieve delivery list",
)
async def get_delivery_list(
    db: AsyncSession = Depends(dependencies.get_db),
):
    """Retrieve all deliveries."""
    return await crud.get_delivery_list(db)


@router.get(
    "/delivery/{delivery_id}",
    response_model=schemas.Delivery,
    summary="Retrieve a delivery by ID",
)
async def get_single_delivery(
    delivery_id: int,
    db: AsyncSession = Depends(dependencies.get_db),
):
    """Retrieve one delivery."""
    delivery = await crud.get_delivery(db, delivery_id)

    if delivery is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Delivery {delivery_id} not found",
        )

    return delivery


@router.delete(
    "/delivery/{delivery_id}",
    response_model=schemas.Delivery,
    summary="Delete a delivery",
)
async def remove_delivery(
    delivery_id: int,
    db: AsyncSession = Depends(dependencies.get_db),
):
    """Delete a delivery."""
    delivery = await crud.delete_delivery(db, delivery_id)

    if delivery is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Delivery {delivery_id} not found",
        )

    return delivery


@router.patch("/delivery/{delivery_id}/status", response_model=schemas.Delivery,
              summary="Update delivery state and notify the order")
async def update_delivery_status(delivery_id: int, update: schemas.DeliveryStatusUpdate,
                                 db: AsyncSession = Depends(dependencies.get_db)):
    delivery = await crud.get_delivery(db, delivery_id)
    if delivery is None:
        raise HTTPException(404, "Delivery not found")
    stages = {"Pending": 0, "Ready": 1, "Sent": 2, "Delivered": 3}
    current = stages.get(delivery.status, 0)
    target = stages[update.status]
    # Retried readiness callbacks must not move a sent/delivered parcel backwards.
    if target < current:
        return delivery
    if current == 0 and target > 1:
        raise HTTPException(409, "Manufacturing has not finished")
    delivery.status = update.status
    if update.tracking_number is not None:
        delivery.tracking_number = update.tracking_number
    await db.commit()
    await db.refresh(delivery)
    await workflow.notify_order(db, delivery)
    return delivery
