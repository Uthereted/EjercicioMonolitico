
# -*- coding: utf-8 -*-
"""FastAPI router definitions."""
import logging
from typing import List
from fastapi import APIRouter, Depends, status
from app.dependencies import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.sql import crud
from ..sql import schemas
from .router_utils import raise_and_log_error

# =============================================================================
# FUTURE MICROSERVICE CONNECTIONS
# =============================================================================
#
# When the other services are ready:
#
# import os
# import httpx
#
# PAYMENT_SERVICE_URL = os.getenv(
#     "PAYMENT_SERVICE_URL",
#     "http://payment-service:8000"
# )
#
# MACHINE_SERVICE_URL = os.getenv(
#     "MACHINE_SERVICE_URL",
#     "http://machine-service:8000"
# )
#
# DELIVERY_SERVICE_URL = os.getenv(
#     "DELIVERY_SERVICE_URL",
#     "http://delivery-service:8000"
# )
#
# CLIENT_SERVICE_URL = os.getenv(
#     "CLIENT_SERVICE_URL",
#     "http://client-service:8000"
# )
#
# =============================================================================

logger = logging.getLogger(__name__)
router = APIRouter()

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
    logger.debug("POST '/order' endpoint called.")

    try:
        # ==========================================================
        # 1. CREATE THE ORDER IN THE ORDER SERVICE DATABASE
        # ==========================================================
        db_order = await crud.create_order_from_schema(
            db,
            order_schema
        )


        # ==========================================================
        # TODO - FUTURE: PAYMENT SERVICE
        # ==========================================================
        #
        # The Order Service will request the Payment Service
        # to create/process the payment for this order.
        #
        # Example:
        #
        # async with httpx.AsyncClient() as client:
        #     payment_response = await client.post(
        #         f"{PAYMENT_SERVICE_URL}/payment",
        #         json={
        #             "order_id": db_order.id,
        #
        #             # TODO:
        #             # Add the fields required by Payment Service
        #             # when the group defines its API contract.
        #         }
        #     )
        #
        #     payment_response.raise_for_status()
        #
        # IMPORTANT:
        # Do not uncomment this until Payment Service exists
        # and its endpoint/body have been agreed with the teammate.


        # ==========================================================
        # TODO - FUTURE: MACHINE SERVICE
        # ==========================================================
        #
        # In the old monolith we called Machine directly.
        #
        # In the microservice architecture we must NOT import Machine.
        # Instead, Order Service will send an HTTP request.
        #
        # Example:
        #
        # async with httpx.AsyncClient() as client:
        #     machine_response = await client.post(
        #         f"{MACHINE_SERVICE_URL}/manufacturing",
        #         json={
        #             "order_id": db_order.id,
        #             "number_of_pieces": db_order.number_of_pieces
        #         }
        #     )
        #
        #     machine_response.raise_for_status()
        #
        # Expected idea:
        #
        # Order Service
        #      |
        #      | POST /manufacturing
        #      v
        # Machine Service
        #
        # Machine Service will then be responsible for
        # creating/managing/manufacturing its own Pieces.


        # ==========================================================
        # TODO - FUTURE: DELIVERY SERVICE
        # ==========================================================
        #
        # Once an order has been created, Order Service may request
        # Delivery Service to create the corresponding delivery.
        #
        # Example:
        #
        # async with httpx.AsyncClient() as client:
        #     delivery_response = await client.post(
        #         f"{DELIVERY_SERVICE_URL}/delivery",
        #         json={
        #             "order_id": db_order.id
        #
        #             # TODO:
        #             # Add delivery/client/address information when
        #             # your group defines the Delivery API contract.
        #         }
        #     )
        #
        #     delivery_response.raise_for_status()


        # ==========================================================
        # TODO - FUTURE: CLIENT SERVICE (OPTIONAL)
        # ==========================================================
        #
        # At the moment OrderPost does NOT contain a client_id.
        #
        # If your group later decides that every order must belong
        # to a registered client, add client_id to OrderPost/Order
        # and optionally validate it against Client Service:
        #
        # async with httpx.AsyncClient() as client:
        #     client_response = await client.get(
        #         f"{CLIENT_SERVICE_URL}/client/{order_schema.client_id}"
        #     )
        #
        #     client_response.raise_for_status()
        #
        # Do NOT implement this just because Client Service exists.
        # Only implement it if the API/domain design says Order
        # needs to validate or retrieve the client.


        # ==========================================================
        # RETURN CREATED ORDER
        # ==========================================================
        return db_order

    except Exception as exc:
        raise_and_log_error(
            logger,
            status.HTTP_409_CONFLICT,
            f"Error creating order: {exc}"
        )

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


@router.patch(
    "/order/{order_id}/status",
    response_model=schemas.Order,
    summary="Update order status",
    tags=["Order"]
)
async def update_order_status(
    order_id: int,
    status_update: schemas.OrderStatusUpdate,
    db: AsyncSession = Depends(get_db)
):
    """
    Update the status of an order.

    FUTURE MICROSERVICES:
    Machine, Delivery or Payment services can call this endpoint
    when they need to notify Order Service about a status change.
    """

    order = await crud.update_order_status(
        db,
        order_id,
        status_update.status
    )

    if not order:
        raise_and_log_error(
            logger,
            status.HTTP_404_NOT_FOUND,
            f"Order {order_id} not found"
        )

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
            "model": schemas.Message,
            "description": "Order not found"
        }
    },
    tags=["Order"]
)
async def remove_order_by_id(
    order_id: int,
    db: AsyncSession = Depends(get_db)
):
    order = await crud.get_order(db, order_id)

    if not order:
        raise_and_log_error(
            logger,
            status.HTTP_404_NOT_FOUND,
            f"Order {order_id} not found"
        )

    return await crud.delete_order(db, order_id)