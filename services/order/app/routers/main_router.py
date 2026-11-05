"""Create orders and query their progress; the saga owns state changes."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.dependencies import get_db
from app.sql import crud, models, schemas
from app import saga, workflow
from app.auth_verifier import verify_jwt

router = APIRouter()


async def require_order(db, order_id):
    order = await crud.get_order(db, order_id)
    if order is None:
        raise HTTPException(404, "Order not found")
    return order


@router.post("/order", response_model=schemas.Order, status_code=202, tags=["Order"])
async def create_order(order_schema: schemas.OrderPost,
                       db: AsyncSession = Depends(get_db),
                       token_payload: dict = Depends(verify_jwt)):
    if "admin" not in token_payload.get("roles", []) and token_payload.get("client_id") != order_schema.client_id:
        raise HTTPException(403, "Forbidden: el pedido debe pertenecer a tu cliente")
    client = await workflow.validate_client(order_schema.client_id)
    address = order_schema.address
    if address is None:
        if not client.address or not client.postal_code:
            raise HTTPException(409, "Client address is not present in the local catalogue yet")
        address = f"{client.address}, {client.postal_code}"
        if len(address) > 500:
            raise HTTPException(422, "Delivery address including postal code must not exceed 500 characters")
    order = models.Order(
        **order_schema.model_dump(exclude={"address"}), address=address, unit_price=workflow.UNIT_PRICE,
        total_price=order_schema.number_of_pieces * workflow.UNIT_PRICE,
        status="PendingPayment",
    )
    db.add(order)
    await db.flush()
    db.add_all([models.Piece(order_id=order.id, status="Queued")
                for _ in range(order.number_of_pieces)])
    await db.flush()
    await saga.start(db, order)
    await db.commit()
    await db.refresh(order)
    return order


@router.get("/order", response_model=List[schemas.Order], tags=["Order"])
async def get_order_list(db: AsyncSession = Depends(get_db), token_payload: dict = Depends(verify_jwt)):
    return await crud.get_order_list(db)


@router.get("/order/{order_id}", response_model=schemas.Order, tags=["Order"])
async def get_single_order(order_id: int, db: AsyncSession = Depends(get_db)):
    return await require_order(db, order_id)


@router.get("/order/{order_id}/pieces", response_model=List[schemas.Piece], tags=["Piece"])
async def get_order_pieces(order_id: int, db: AsyncSession = Depends(get_db)):
    await require_order(db, order_id)
    return await workflow.pieces_for_order(db, order_id)
