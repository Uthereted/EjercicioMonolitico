# -*- coding: utf-8 -*-
"""FastAPI router definitions."""
import logging
from typing import List
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.business_logic.async_machine import Machine
from app.dependencies import get_db, get_machine
from app.sql import crud
from ..sql import schemas
from .router_utils import raise_and_log_error

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/",
    summary="Health check endpoint",
    response_model=schemas.Message,
)
async def health_check():
    """Endpoint to check if everything started correctly."""
    logger.debug("GET '/' endpoint called.")
    return {
        "detail": "OK"
    }


# Machine ##########################################################################################
@router.get(
    "/machine/status",
    summary="Retrieve machine status",
    response_model=schemas.MachineStatusResponse,
    tags=['Machine']
)
async def machine_status(
        my_machine: Machine = Depends(get_machine)
):
    """Retrieve machine status"""
    logger.debug("GET '/machine/status' endpoint called.")
    working_piece_id = None
    if my_machine.working_piece is not None:
        working_piece_id = my_machine.working_piece['id']

    queue = await my_machine.list_queued_pieces()

    return schemas.MachineStatusResponse(
        status=my_machine.status,
        working_piece=working_piece_id,
        queue=queue
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
    db: AsyncSession = Depends(get_db),
    machine: Machine = Depends(get_machine)
):
    """Create single order endpoint."""
    logger.debug("POST '/order' endpoint called.")
    try:
        db_order = await crud.create_order_from_schema(db, order_schema)

        for _ in range(order_schema.number_of_pieces):
            db_order = await crud.add_piece_to_order(db, db_order)
        await machine.add_pieces_to_queue(db_order.pieces)
        return db_order
    except Exception as exc:  # @ToDo: To broad exception
        raise_and_log_error(logger, status.HTTP_409_CONFLICT, f"Error creating order: {exc}")


@router.get(
    "/order",
    response_model=List[schemas.Order],
    summary="Retrieve order list",
    tags=["Order", "List"]  # Optional so it appears grouped in documentation
)
async def get_order_list(
        db: AsyncSession = Depends(get_db)
):
    """Retrieve order list"""
    logger.debug("GET '/order' endpoint called.")
    order_list = await crud.get_order_list(db)
    return order_list


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
        db: AsyncSession = Depends(get_db),
        my_machine: Machine = Depends(get_machine)
):
    """Remove order"""
    logger.debug("DELETE '/order/%i' endpoint called.", order_id)
    order = await crud.get_order(db, order_id)
    if not order:
        raise_and_log_error(logger, status.HTTP_404_NOT_FOUND, f"Order {order_id} not found")
    await my_machine.remove_pieces_from_queue(order.pieces)
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
    return await crud.get_piece(db, piece_id)
