from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from .. import dependencies
from ..sql import crud, schemas
from .. import workflow
from ..auth_verifier import verify_jwt

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
    summary="Legacy direct delivery creation (disabled; use the order saga)",
    status_code=status.HTTP_201_CREATED,
)
async def create_delivery(
    delivery_schema: schemas.DeliveryPost,
    db: AsyncSession = Depends(dependencies.get_db),
    token_payload: dict = Depends(verify_jwt)
):
    """Delivery creation is owned by the order saga."""
    raise HTTPException(409, "Direct delivery creation is disabled; use the order saga")


@router.get(
    "/delivery",
    response_model=List[schemas.Delivery],
    summary="Retrieve delivery list",
)
async def get_delivery_list(
    db: AsyncSession = Depends(dependencies.get_db),
    token_payload: dict = Depends(verify_jwt)
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
    token_payload: dict = Depends(verify_jwt)
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
    token_payload: dict = Depends(verify_jwt)
):
    """Delete a delivery."""
    delivery = await crud.delete_delivery(db, delivery_id)

    if delivery is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Delivery {delivery_id} not found",
        )

    return delivery

@router.patch(
    "/delivery/{delivery_id}/status",
    response_model=schemas.Delivery,
    summary="Update delivery state and notify the order",
)
async def update_delivery_status(
    delivery_id: int,
    update: schemas.DeliveryStatusUpdate,
    db: AsyncSession = Depends(dependencies.get_db),
    token_payload: dict = Depends(verify_jwt)
):
    return await workflow.set_delivery_status(db, delivery_id, update)
