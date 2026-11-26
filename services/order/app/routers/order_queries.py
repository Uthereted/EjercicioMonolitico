"""Read-only order API with the same authentication and ownership rules."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.dependencies import get_db
from app.sql import crud, schemas
from app import workflow
from app.auth_verifier import verify_jwt

router = APIRouter()


async def require_order(db, order_id, token_payload):
    order = await crud.get_order(db, order_id)
    if order is None:
        raise HTTPException(404, "Order not found")
    if "admin" not in token_payload.get("roles", []):
        client_id = token_payload.get("client_id")
        if client_id is None or order.client_id != client_id:
            raise HTTPException(403, "You can only read your own orders")
    return order


@router.get("/order", response_model=List[schemas.Order], tags=["Order"])
async def get_order_list(db: AsyncSession = Depends(get_db), token_payload: dict = Depends(verify_jwt)):
    if "admin" in token_payload.get("roles", []):
        return await crud.get_order_list(db)
    client_id = token_payload.get("client_id")
    if client_id is None:
        return []
    return await crud.get_order_list(db, client_id=client_id)


@router.get("/order/{order_id}", response_model=schemas.Order, tags=["Order"])
async def get_single_order(order_id: int, db: AsyncSession = Depends(get_db),
                           token_payload: dict = Depends(verify_jwt)):
    return await require_order(db, order_id, token_payload)


@router.get("/order/{order_id}/pieces", response_model=List[schemas.Piece], tags=["Piece"])
async def get_order_pieces(order_id: int, db: AsyncSession = Depends(get_db),
                           token_payload: dict = Depends(verify_jwt)):
    await require_order(db, order_id, token_payload)
    return await workflow.pieces_for_order(db, order_id)
