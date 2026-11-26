"""Read-only payment API: balance queries never create accounts."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.dependencies import get_db, check_client_ownership
from app.sql import crud, schemas
from app.auth_verifier import verify_jwt

router = APIRouter()


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
    check_client_ownership(client_id, token_payload)
    account = await crud.get_account_by_client_id(db, client_id)
    balance = 0
    if account is not None:
        balance = account.balance
    return schemas.BalanceResponse(client_id=client_id, balance=balance)
