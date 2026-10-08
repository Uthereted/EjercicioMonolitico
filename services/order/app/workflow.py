"""Coordinate REST calls and retry durable pending orders after outages."""
import asyncio
import logging
import os
from collections import defaultdict
import httpx
from fastapi import HTTPException
from sqlalchemy import select
from .sql import database, models

logger = logging.getLogger(__name__)
UNIT_PRICE = 10
CLIENT_URL = os.getenv("CLIENT_SERVICE_URL", "http://localhost:13005").rstrip("/")
PAYMENT_URL = os.getenv("PAYMENT_SERVICE_URL", "http://localhost:13006").rstrip("/")
MACHINE_URL = os.getenv("MACHINE_SERVICE_URL", "http://localhost:13002").rstrip("/")
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
        async with httpx.AsyncClient(timeout=5) as client:
            if not order.payment_completed:
                try:
                    response = await client.post(
                        f"{PAYMENT_URL}/payment/{order.client_id}/charge",
                        json={"amount": order.total_price, "order_id": order.id},
                    )
                    if response.status_code in (404, 409):
                        order.status = "Rejected"
                        order.integration_error = response.json().get("detail", "Payment rejected")
                        await db.commit()
                        await db.refresh(order)
                        raise HTTPException(response.status_code, order.integration_error)
                    response.raise_for_status()
                except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                    order.status = "PendingPayment"
                    order.integration_error = f"Payment pending: {exc}"
                    await db.commit()
                    await db.refresh(order)
                    return order
                order.payment_completed = True
                order.status = "Accepted"
                order.integration_error = None
                await db.commit()
                await db.refresh(order)

            errors = []
            pieces = await pieces_for_order(db, order.id)
            if not order.manufacturing_requested:
                try:
                    response = await client.post(
                        f"{MACHINE_URL}/machine/queue",
                        json=[piece.id for piece in pieces if piece.status != "Manufactured"],
                    )
                    response.raise_for_status()
                    order.manufacturing_requested = True
                    await db.commit()
                    await db.refresh(order)
                except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                    errors.append(f"Manufacturing pending: {exc}")

            if order.delivery_id is None:
                try:
                    response = await client.post(
                        f"{DELIVERY_URL}/deliveries/delivery",
                        json={"order_id": order.id, "client_id": order.client_id,
                              "address": order.address},
                    )
                    response.raise_for_status()
                    order.delivery_id = response.json()["id"]
                    await db.commit()
                    await db.refresh(order)
                except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                    errors.append(f"Delivery creation pending: {exc}")

            # Read again: machine callbacks may have arrived during REST calls.
            for piece in pieces:
                await db.refresh(piece)
            finished = len(pieces) == order.number_of_pieces and all(
                piece.status == "Manufactured" for piece in pieces
            )
            if finished and order.delivery_id is not None:
                try:
                    response = await client.patch(
                        f"{DELIVERY_URL}/deliveries/delivery/{order.delivery_id}/status",
                        json={"status": "Ready"},
                    )
                    response.raise_for_status()
                except (httpx.RequestError, httpx.HTTPStatusError) as exc:
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
                    models.Order.status.in_(["PendingPayment", "Accepted", "Pending", "Manufacturing"]),
                ))).all())
                for order in orders:
                    try:
                        await resume_order(db, order)
                    except HTTPException:
                        logger.info("Payment rejected for order %s", order.id)
        except Exception:
            logger.exception("Pending order retry failed; will retry later")
