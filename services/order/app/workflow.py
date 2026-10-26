"""Persist order facts; subscribers decide their own next steps."""
from collections import defaultdict
import asyncio
from fastapi import HTTPException
from sqlalchemy import select
from .events import record_event
from .sql import database, models

UNIT_PRICE = 10
locks = defaultdict(asyncio.Lock)


async def validate_client(client_id):
    async with database.SessionLocal() as db:
        if await db.get(models.KnownClient, client_id) is None:
            raise HTTPException(404, "Client is not present in the local catalogue yet")


async def pieces_for_order(db, order_id):
    return list((await db.scalars(select(models.Piece).where(
        models.Piece.order_id == order_id).order_by(models.Piece.id))).all())


async def stage_order_facts(db, order):
    if order.client_id is None or order.status in ("Rejected", "Delivered"):
        return
    if not order.payment_completed:
        await record_event(db, "order.created", {
            "order_id": order.id, "client_id": order.client_id, "amount": order.total_price,
        }, f"order.created:{order.id}")
        return
    pieces = await pieces_for_order(db, order.id)
    payload = {"order_id": order.id, "client_id": order.client_id, "amount": order.total_price,
               "address": order.address, "piece_ids": [piece.id for piece in pieces],
               "manufactured_piece_ids": [piece.id for piece in pieces if piece.status == "Manufactured"]}
    await record_event(db, "order.paid", payload, f"order.paid:{order.id}")
    finished = len(pieces) == order.number_of_pieces and all(piece.status == "Manufactured" for piece in pieces)
    if finished:
        await record_event(db, "order.manufactured", payload, f"order.manufactured:{order.id}")
    if order.status not in ("ReadyForDelivery", "Delivered"):
        order.status = "Manufacturing"
    order.integration_error = None


async def resume_order(db, order):
    async with locks[order.id]:
        await db.refresh(order)
        await stage_order_facts(db, order)
        await db.commit()
        await db.refresh(order)
        return order


async def bootstrap():
    # Bridge existing databases to the event choreography without recharging.
    async with database.SessionLocal() as db:
        orders = list((await db.scalars(select(models.Order).where(
            models.Order.client_id.is_not(None),
            models.Order.status.not_in(["Rejected", "Delivered"]),
        ))).all())
        for order in orders:
            await stage_order_facts(db, order)
        await db.commit()
