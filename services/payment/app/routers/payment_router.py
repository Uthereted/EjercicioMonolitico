# -*- coding: utf-8 -*-
"""FastAPI router definitions."""
import logging
import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, client_exists
from app.sql import crud
from app.auth_verifier import verify_jwt  # Importación del verificador JWT
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


async def _ensure_client_exists(client_id: int):
    """Validates the client exists in the Client service, raising HTTP errors otherwise."""
    try:
        exists = await client_exists(client_id)
    except (httpx.RequestError, httpx.HTTPStatusError):
        raise_and_log_error(
            logger,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Client service is currently unreachable"
        )
        return  # pragma: no cover - raise_and_log_error always raises
    if not exists:
        raise_and_log_error(
            logger, status.HTTP_404_NOT_FOUND, f"Client {client_id} not found"
        )


def _check_client_ownership(client_id: int, token_payload: dict):
    """
    Verifica que el token pertenezca al propio cliente o sea una petición inter-servicio/admin.
    """
    # Si es una llamada interna de microservicio o admin, se permite la operación
    if token_payload.get("service") is True or "admin" in token_payload.get("roles", []):
        return

    # User IDs and client IDs belong to separate tables.
    if token_payload.get("client_id") != client_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: No tienes permisos para acceder o modificar los fondos de este cliente"
        )


# Payment ###########################################################################################
@router.post(
    "/payment/{client_id}/deposit",
    response_model=schemas.Account,
    summary="Perform a deposit into a client's account",
    status_code=status.HTTP_201_CREATED,
    tags=["Payment"]
)
async def deposit(
    client_id: int,
    deposit_schema: schemas.DepositPost,
    db: AsyncSession = Depends(get_db),
    token_payload: dict = Depends(verify_jwt)  # Inyección de seguridad JWT
):
    """Perform a deposit endpoint. Called directly by the UI (step 2 in the flow)."""
    logger.debug("POST '/payment/%i/deposit' endpoint called.", client_id)
    _check_client_ownership(client_id, token_payload)
    await _ensure_client_exists(client_id)
    return await crud.deposit(db, client_id, deposit_schema.amount)


@router.post(
    "/payment/{client_id}/charge",
    response_model=schemas.Account,
    summary="Charge a client's account to pay for an order",
    responses={
        status.HTTP_409_CONFLICT: {
            "model": schemas.Message, "description": "Insufficient funds"
        }
    },
    tags=["Payment"]
)
async def charge(
    client_id: int,
    charge_schema: schemas.ChargePost,
    db: AsyncSession = Depends(get_db),
    token_payload: dict = Depends(verify_jwt)  # Inyección de seguridad JWT
):
    """
    Charge endpoint. Called by the Order service (step 3.1 in the flow), NOT by the UI.
    Returns 409 if the client does not have enough balance.
    """
    logger.debug("POST '/payment/%i/charge' endpoint called.", client_id)
    _check_client_ownership(client_id, token_payload)
    await _ensure_client_exists(client_id)
    try:
        return await crud.charge(
            db, client_id, charge_schema.amount, charge_schema.order_id
        )
    except (crud.InsufficientFundsError, crud.PaymentConflictError) as exc:
        raise_and_log_error(logger, status.HTTP_409_CONFLICT, str(exc))


@router.get(
    "/payment/{client_id}/balance",
    response_model=schemas.BalanceResponse,
    summary="Retrieve a client's current balance",
    tags=["Payment"]
)
async def get_balance(
    client_id: int,
    db: AsyncSession = Depends(get_db),
    token_payload: dict = Depends(verify_jwt)  # Inyección de seguridad JWT
):
    """Retrieve a client's current balance."""
    logger.debug("GET '/payment/%i/balance' endpoint called.", client_id)
    _check_client_ownership(client_id, token_payload)
    account = await crud.get_or_create_account(db, client_id)
    return schemas.BalanceResponse(client_id=client_id, balance=account.balance)
