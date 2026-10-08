"""Order API and internal manufacturing/delivery callbacks."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.dependencies import get_db
from app.sql import crud, models, schemas
from app import workflow

router = APIRouter()


async def require_order(db, order_id):
    order = await crud.get_order(db, order_id)
    if order is None:
        raise HTTPException(404, "Order not found")
    return order


@router.post("/order", response_model=schemas.Order, status_code=201, tags=["Order"])
async def create_order(order_schema: schemas.OrderPost, response: Response,
                       db: AsyncSession = Depends(get_db)):
    await workflow.validate_client(order_schema.client_id)
    order = models.Order(
        **order_schema.model_dump(), unit_price=workflow.UNIT_PRICE,
        total_price=order_schema.number_of_pieces * workflow.UNIT_PRICE,
        status="PendingPayment",
    )
    db.add(order)
    await db.flush()
    db.add_all([models.Piece(order_id=order.id, status="Queued")
                for _ in range(order.number_of_pieces)])
    await db.commit()
    await db.refresh(order)
    await workflow.resume_order(db, order)
    if not order.payment_completed:
        response.status_code = 202
    return order


@router.get("/order", response_model=List[schemas.Order], tags=["Order"])
async def get_order_list(db: AsyncSession = Depends(get_db)):
    return await crud.get_order_list(db)


@router.get("/order/{order_id}", response_model=schemas.Order, tags=["Order"])
async def get_single_order(order_id: int, db: AsyncSession = Depends(get_db)):
    return await require_order(db, order_id)


@router.post("/order/{order_id}/retry", response_model=schemas.Order, tags=["Order"])
async def retry_order(order_id: int, db: AsyncSession = Depends(get_db)):
    return await workflow.resume_order(db, await require_order(db, order_id))


@router.get("/order/{order_id}/pieces", response_model=List[schemas.Piece], tags=["Piece"])
async def get_order_pieces(order_id: int, db: AsyncSession = Depends(get_db)):
    await require_order(db, order_id)
    return await workflow.pieces_for_order(db, order_id)


@router.get("/piece", response_model=List[schemas.Piece], tags=["Piece"])
async def get_pieces(db: AsyncSession = Depends(get_db)):
    return list((await db.scalars(select(models.Piece))).all())


@router.get("/piece/by-status/{piece_status}", response_model=List[schemas.Piece], tags=["Piece"])
async def get_pieces_by_status(piece_status: str, db: AsyncSession = Depends(get_db)):
    # Unpaid orders must never enter a machine, including on restart recovery.
    return list((await db.scalars(select(models.Piece).join(models.Order).where(
        models.Piece.status == piece_status, models.Order.payment_completed.is_(True),
        models.Order.status.not_in(["Rejected", "Delivered"]),
    ))).all())


@router.get("/piece/{piece_id}", response_model=schemas.Piece, tags=["Piece"])
async def get_piece(piece_id: int, db: AsyncSession = Depends(get_db)):
    piece = await db.get(models.Piece, piece_id)
    if piece is None:
        raise HTTPException(404, "Piece not found")
    return piece


@router.patch("/piece/{piece_id}", response_model=schemas.Piece, tags=["Piece"])
async def update_piece(piece_id: int, update: schemas.PieceStatusUpdate,
                       db: AsyncSession = Depends(get_db)):
    piece = await get_piece(piece_id, db)
    order = await require_order(db, piece.order_id)
    if not order.payment_completed:
        raise HTTPException(409, "Order payment has not been accepted")
    if piece.status != "Manufactured":
        if update.status == "Manufactured" and piece.status != "Manufacturing":
            raise HTTPException(409, "Piece has not started manufacturing")
        piece.status = update.status
        await db.commit()
        await db.refresh(piece)
    if update.status == "Manufactured":
        await workflow.resume_order(db, order)
        await db.refresh(piece)
    return piece


@router.patch("/order/{order_id}/status", response_model=schemas.Order, tags=["Order"])
async def update_order_status(order_id: int, update: schemas.OrderStatusUpdate,
                              db: AsyncSession = Depends(get_db)):
    async with workflow.locks[order_id]:
        order = await require_order(db, order_id)
        pieces = await workflow.pieces_for_order(db, order_id)
        if not order.payment_completed or len(pieces) != order.number_of_pieces or any(
            piece.status != "Manufactured" for piece in pieces
        ):
            raise HTTPException(409, "Order manufacturing has not finished")
        if update.status == "Delivered" and order.delivery_id is None:
            raise HTTPException(409, "Order has no delivery")
        if order.status != "Delivered":
            order.status = update.status
            order.integration_error = None
            await db.commit()
            await db.refresh(order)
        return order


@router.delete("/order/{order_id}", response_model=schemas.Order, tags=["Order"])
async def remove_order_by_id(order_id: int, db: AsyncSession = Depends(get_db)):
    async with workflow.locks[order_id]:
        order = await require_order(db, order_id)
        if order.client_id is not None:
            raise HTTPException(409, "Workflow orders are retained to prevent duplicate charges")
        return await crud.delete_order(db, order_id)
