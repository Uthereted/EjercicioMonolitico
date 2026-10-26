"""Continue paid orders through manufacturing events and delivery REST calls."""
import asyncio
import logging
import os
from collections import defaultdict
import httpx
from fastapi import HTTPException
from sqlalchemy import select
from . import messaging
from .sql import database, models

logger = logging.getLogger(__name__)
UNIT_PRICE = 10
CLIENT_URL = os.getenv("CLIENT_SERVICE_URL", "http://localhost:13005").rstrip("/")
DELIVERY_URL = os.getenv("DELIVERY_SERVICE_URL", "http://localhost:13004").rstrip("/")
RETRY_SECONDS = float(os.getenv("REST_RETRY_SECONDS", "5"))
locks = defaultdict(asyncio.Lock)


async def validate_client(client_id):
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{CLIENT_URL}/clients/{client_id}")
            if response.status_code == 404:
                raise HTTPException(404, "Client not found")
            response.raise_for_status()
    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
        raise HTTPException(503, "Client service is unavailable") from exc


async def pieces_for_order(db, order_id):
    return list((await db.scalars(select(models.Piece).where(
        models.Piece.order_id == order_id
    ).order_by(models.Piece.id))).all())


async def resume_order(db, order):
    async with locks[order.id]:
        await db.refresh(order)
        if order.status in ("Rejected", "Delivered") or order.client_id is None:
            return order
        if not order.payment_completed:
            return order
        async with httpx.AsyncClient(timeout=5) as client:
            errors = []
            pieces = await pieces_for_order(db, order.id)
            if not order.manufacturing_requested:
                try:
                    piece_ids = [piece.id for piece in pieces if piece.status != "Manufactured"]
                    if piece_ids:
                        await messaging.publish_event(
                            "machine.pieces.requested", "machine.events",
                            {"order_id": order.id, "piece_ids": piece_ids},
                            message_id=f"machine.pieces.requested:{order.id}",
                        )
                    order.manufacturing_requested = True
                    await db.commit()
                    await db.refresh(order)
                except Exception as exc:
                    errors.append(f"Manufacturing pending: {exc}")

            if order.delivery_id is None:
                try:
                    secret = os.getenv("ORDER_SERVICE_AUTH_SECRET", "")
                    if not secret:
                        raise ValueError("ORDER_SERVICE_AUTH_SECRET is not configured")
                    token_response = await client.post(
                        f"{CLIENT_URL}/auth/service-token",
                        auth=httpx.BasicAuth("order-service", secret),
                    )
                    token_response.raise_for_status()
                    access_token = token_response.json()["access_token"]
                    response = await client.post(
                        f"{DELIVERY_URL}/deliveries/delivery",
                        json={"order_id": order.id, "client_id": order.client_id,
                              "address": order.address},
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                    response.raise_for_status()
                    order.delivery_id = response.json()["id"]
                    await db.commit()
                    await db.refresh(order)
                except (httpx.RequestError, httpx.HTTPStatusError, ValueError, KeyError) as exc:
                    errors.append(f"Delivery creation pending: {exc}")

            # Read again: piece events may have arrived during the calls above.
            for piece in pieces:
                await db.refresh(piece)
            finished = len(pieces) == order.number_of_pieces and all(
                piece.status == "Manufactured" for piece in pieces
            )
            if (
                    finished
                    and order.delivery_id is not None
                    and not order.delivery_ready_published
            ):
                try:
                    await messaging.publish_event(
                        "order.delivery.ready",
                        "delivery.events",
                        {
                            "order_id": order.id,
                            "delivery_id": order.delivery_id,
                            "status": "Ready",
                        },
                        message_id=f"order.delivery.ready:{order.id}",
                    )
                    order.delivery_ready_published = True
                    await db.commit()
                    await db.refresh(order)
                except Exception as exc:
                    errors.append(f"Delivery readiness pending: {exc}")

            if errors:
                order.status = "Pending"
                order.integration_error = "; ".join(errors)
            else:
                order.status = "ReadyForDelivery" if finished else "Manufacturing"
                order.integration_error = None
            await db.commit()
            await db.refresh(order)
            return order


async def retry_pending_orders():
    while True:
        await asyncio.sleep(RETRY_SECONDS)
        try:
            async with database.SessionLocal() as db:
                orders = list((await db.scalars(select(models.Order).where(
                    models.Order.client_id.is_not(None),
                    models.Order.payment_completed.is_(True),
                    models.Order.status.in_(["Accepted", "Pending", "Manufacturing"]),
                ))).all())
                for order in orders:
                    await resume_order(db, order)
        except Exception:
            logger.exception("Pending order retry failed; will retry later")
