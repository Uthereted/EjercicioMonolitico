"""Read-only client API, separate from registration and deletion commands."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.dependencies import get_db
from app.sql import schemas
from app import queries
from app.auth_verifier import verify_jwt, is_admin

router = APIRouter()


@router.get("/clients/{client_id}", response_model=schemas.ClientResponse, tags=["Client"])
async def read_client(client_id: int, db: AsyncSession = Depends(get_db),
                      token_payload: dict = Depends(verify_jwt)):
    if not is_admin(token_payload) and token_payload.get("client_id") != client_id:
        raise HTTPException(403, "You can only read your own client")
    profiles = await queries.get_client_profiles(db, client_id=client_id)
    if not profiles:
        raise HTTPException(status_code=404, detail="Client not found")
    return profiles[0]


@router.get("/clients", response_model=List[schemas.ClientResponse], tags=["Client"])
async def read_clients(db: AsyncSession = Depends(get_db), token_payload: dict = Depends(verify_jwt)):
    if is_admin(token_payload):
        return await queries.get_client_profiles(db)
    if token_payload.get("service_name") is not None:
        raise HTTPException(403, "Services cannot list clients")
    client_id = token_payload.get("client_id")
    if client_id is None:
        return []
    return await queries.get_client_profiles(db, client_id=client_id)
