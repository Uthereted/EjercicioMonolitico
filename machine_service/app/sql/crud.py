# -*- coding: utf-8 -*-
"""Database access functions required by the Machine simulator."""
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.sql.models import Piece, Order


async def get_piece_list_by_status(db: AsyncSession, piece_status: str):
    """Return all pieces with the given status."""
    result = await db.execute(select(Piece).where(Piece.status == piece_status))
    return result.scalars().all()


async def get_piece(db: AsyncSession, piece_id: int):
    """Return a single piece by id."""
    result = await db.execute(select(Piece).where(Piece.id == piece_id))
    return result.scalar_one_or_none()


async def update_piece_status(db: AsyncSession, piece_id: int, piece_status: str):
    """Update a piece's status and return it."""
    piece = await get_piece(db, piece_id)
    piece.status = piece_status
    await db.commit()
    await db.refresh(piece)
    return piece


async def update_piece_manufacturing_date_to_now(db: AsyncSession, piece_id: int):
    """Set a piece's manufacturing_date to now and return it."""
    piece = await get_piece(db, piece_id)
    piece.manufacturing_date = datetime.utcnow()
    await db.commit()
    await db.refresh(piece)
    return piece


async def get_order(db: AsyncSession, order_id: int):
    """Return a single order (with its pieces) by id."""
    result = await db.execute(
        select(Order).options(selectinload(Order.pieces)).where(Order.id == order_id)
    )
    return result.scalar_one_or_none()


async def update_order_status(db: AsyncSession, order_id: int, order_status: str):
    """Update an order's status and return it."""
    order = await get_order(db, order_id)
    order.status = order_status
    await db.commit()
    await db.refresh(order)
    return order
