# -*- coding: utf-8 -*-
"""FastAPI router definitions."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, client_exists
from app.sql import crud
from app.auth_verifier import verify_jwt  # Importación del verificador JWT
from ..sql import schemas

router = APIRouter()


def _check_client_ownership(client_id: int, token_payload: dict):
    """Allow an administrator or the client identified by the signed token."""
    if "admin" in token_payload.get("roles", []):
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
    _check_client_ownership(client_id, token_payload)
    if not await client_exists(client_id):
        raise HTTPException(404, f"Client {client_id} not found")
    return await crud.deposit(db, client_id, deposit_schema.amount)


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
    _check_client_ownership(client_id, token_payload)
    account = await crud.get_or_create_account(db, client_id)
    return schemas.BalanceResponse(client_id=client_id, balance=account.balance)
