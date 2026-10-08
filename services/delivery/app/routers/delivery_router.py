from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from .. import dependencies
from ..sql import crud, schemas

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