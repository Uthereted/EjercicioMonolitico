import logging
from typing import List
import httpx
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.sql import crud, schemas, models
from .router_utils import raise_and_log_error

logger = logging.getLogger(__name__)
router = APIRouter()

MACHINE_SERVICE_URL = "http://localhost:8001"


@router.get(
    "/",
    summary="Health check endpoint",
    response_model=schemas.Message,
)
async def health_check():
    """Endpoint to check if everything started correctly."""
    logger.debug("GET '/' endpoint called.")
    return {"detail": "OK"}


# Machine ##########################################################################################
@router.get(
    "/machine/status",
    summary="Retrieve machine status",
    response_model=schemas.MachineStatusResponse,
    tags=['Machine']
)
async def machine_status():
    """Retrieve machine status by asking Machine Service over HTTP."""
    logger.debug("GET '/machine/status' endpoint called.")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{MACHINE_SERVICE_URL}/machine/status")
            if response.status_code == status.HTTP_200_OK:
                return response.json()
            raise_and_log_error(
                logger,
                response.status_code,
                "Machine service returned an error."
            )
        except httpx.RequestError as exc:
            raise_and_log_error(
                logger,
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"Machine service unavailable: {exc}"
            )


# Orders ###########################################################################################
@router.post(
    "/order",
    response_model=schemas.Order,
    summary="Create single order",
    status_code=status.HTTP_201_CREATED,
    tags=["Order"]
)
async def create_order(
    order_schema: schemas.OrderPost,
    db: AsyncSession = Depends(get_db)
):
    """Create single order endpoint."""
    logger.debug("POST '/order' endpoint called.")
    try:
        db_order = await crud.create_order_from_schema(db, order_schema)

        for _ in range(order_schema.number_of_pieces):
            db_order = await crud.add_piece_to_order(db, db_order)

        # Enviar los IDs de las piezas al microservicio de la Máquina vía HTTP
        piece_ids = [piece.id for piece in db_order.pieces]
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{MACHINE_SERVICE_URL}/machine/queue",
                json=piece_ids
            )

        return db_order
    except Exception as exc:
        raise_and_log_error(logger, status.HTTP_409_CONFLICT, f"Error creating order: {exc}")


@router.get(
    "/order",
    response_model=List[schemas.Order],
    summary="Retrieve order list",
    tags=["Order", "List"]
)
async def get_order_list(
        db: AsyncSession = Depends(get_db)
):
    """Retrieve order list"""
    logger.debug("GET '/order' endpoint called.")
    return await crud.get_order_list(db)


@router.get(
    "/order/{order_id}",
    summary="Retrieve single order by id",
    responses={
        status.HTTP_200_OK: {
            "model": schemas.Order,
            "description": "Requested Order."
        },
        status.HTTP_404_NOT_FOUND: {
            "model": schemas.Message, "description": "Order not found"
        }
    },
    tags=['Order']
)
async def get_single_order(
        order_id: int,
        db: AsyncSession = Depends(get_db)
):
    """Retrieve single order by id"""
    logger.debug("GET '/order/%i' endpoint called.", order_id)
    order = await crud.get_order(db, order_id)
    if not order:
        raise_and_log_error(logger, status.HTTP_404_NOT_FOUND, f"Order {order_id} not found")
    return order


@router.delete(
    "/order/{order_id}",
    summary="Delete order",
    responses={
        status.HTTP_200_OK: {
            "model": schemas.Order,
            "description": "Order successfully deleted."
        },
        status.HTTP_404_NOT_FOUND: {
            "model": schemas.Message, "description": "Order not found"
        }
    },
    tags=["Order"]
)
async def remove_order_by_id(
        order_id: int,
        db: AsyncSession = Depends(get_db)
):
    """Remove order and notify Machine Service to cancel queued pieces."""
    logger.debug("DELETE '/order/%i' endpoint called.", order_id)
    order = await crud.get_order(db, order_id)
    if not order:
        raise_and_log_error(logger, status.HTTP_404_NOT_FOUND, f"Order {order_id} not found")

    # Notificar a la máquina que elimine de la cola las piezas
    async with httpx.AsyncClient() as client:
        for piece in order.pieces:
            await client.delete(f"{MACHINE_SERVICE_URL}/machine/queue/{piece.id}")

    return await crud.delete_order(db, order_id)


# Pieces ###########################################################################################
@router.get(
    "/piece",
    response_model=List[schemas.Piece],
    summary="retrieve piece list",
    tags=["Piece", "List"]
)
async def get_piece_list(
        db: AsyncSession = Depends(get_db)
):
    """Retrieve the list of pieces."""
    logger.debug("GET '/piece' endpoint called.")
    return await crud.get_piece_list(db)


@router.get(
    "/piece/by-status/{status_name}",
    response_model=List[schemas.Piece],
    summary="Retrieve pieces by status",
    tags=["Piece"]
)
async def get_pieces_by_status(
        status_name: str,
        db: AsyncSession = Depends(get_db)
):
    """Endpoint used by Machine Service at startup to recover queued pieces."""
    logger.debug("GET '/piece/by-status/%s' endpoint called.", status_name)
    return await crud.get_piece_list_by_status(db, status_name)


@router.get(
    "/piece/{piece_id}",
    summary="Retrieve single piece by id",
    response_model=schemas.Piece,
    tags=['Piece']
)
async def get_single_piece(
        piece_id: int,
        db: AsyncSession = Depends(get_db)
):
    """Retrieve single piece by id"""
    logger.debug("GET '/piece/%i' endpoint called.", piece_id)
    piece = await crud.get_piece(db, piece_id)
    if not piece:
        raise_and_log_error(logger, status.HTTP_404_NOT_FOUND, f"Piece {piece_id} not found")
    return piece


@router.patch(
    "/piece/{piece_id}",
    summary="Update piece status",
    response_model=schemas.Piece,
    tags=['Piece']
)
async def update_piece_status(
        piece_id: int,
        payload: schemas.PieceUpdate,
        db: AsyncSession = Depends(get_db)
):
    """Endpoint used by Machine Service to update piece status during manufacturing."""
    logger.debug("PATCH '/piece/%i' endpoint called with status %s", piece_id, payload.status)
    piece = await crud.update_piece_status(db, piece_id, payload.status)
    if not piece:
        raise_and_log_error(logger, status.HTTP_404_NOT_FOUND, f"Piece {piece_id} not found")

    if payload.status == models.Piece.STATUS_MANUFACTURED:
        piece = await crud.update_piece_manufacturing_date_to_now(db, piece_id)
        order = await crud.get_order(db, piece.order_id)
        if order and all(p.status == models.Piece.STATUS_MANUFACTURED for p in order.pieces):
            await crud.update_order_status(db, piece.order_id, models.Order.STATUS_FINISHED)

    return piece
