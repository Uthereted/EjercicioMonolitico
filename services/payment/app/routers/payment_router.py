from my_chassis.auth import get_bearer_token
# -*- coding: utf-8 -*-
"""FastAPI router definitions."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, client_exists, check_client_ownership
from app.sql import crud
from app.auth_verifier import verify_jwt  # Importación del verificador JWT
from ..sql import schemas

router = APIRouter()


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
    token_payload: dict = Depends(verify_jwt),
    access_token: str = Depends(get_bearer_token)
):
    """Perform a deposit endpoint. Called directly by the UI (step 2 in the flow)."""
    check_client_ownership(client_id, token_payload)
    if not await client_exists(client_id, access_token):
        raise HTTPException(404, f"Client {client_id} not found")
    return await crud.deposit(db, client_id, deposit_schema.amount)
