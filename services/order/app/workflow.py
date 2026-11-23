"""Persist order facts; subscribers decide their own next steps."""
from fastapi import HTTPException
from pydantic import ValidationError
from my_chassis.discovery import query_service
from sqlalchemy import select
from .contracts import ClientDetails
from .events import record_event
from .sql import database, models

UNIT_PRICE = 10


async def validate_client(client_id, access_token):
    data = await query_service('client-service', f'/clients/{client_id}', access_token=access_token)
    try:
        client = ClientDetails.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(502, 'Invalid client query response') from exc
    if client.id != client_id:
        raise HTTPException(502, 'Invalid client query response')
    return client


async def pieces_for_order(db, order_id):
    return list((await db.scalars(select(models.Piece).where(
        models.Piece.order_id == order_id).order_by(models.Piece.id))).all())


async def stage_order_facts(db, order):
    order_saga = await db.scalar(select(models.OrderSaga).where(models.OrderSaga.order_id == order.id))
    if order_saga is not None and order_saga.state != "Approved":
        # Retries and startup must not also launch the legacy payment choreography.
        from .saga import stage_requests
        await stage_requests(db, order, order_saga)
        return
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


async def bootstrap():
    # Bridge existing databases to the event choreography without recharging.
    async with database.SessionLocal() as db:
        from .saga_history import snapshot_existing
        await snapshot_existing(db)
        orders = list((await db.scalars(select(models.Order).where(
            models.Order.client_id.is_not(None),
            models.Order.status.not_in(["Rejected", "Delivered"]),
        ))).all())
        for order in orders:
            await stage_order_facts(db, order)
        await db.commit()
