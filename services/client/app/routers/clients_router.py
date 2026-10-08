from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.dependencies import get_db
from app.sql import crud, schemas

router = APIRouter()

@router.post("/clients", response_model=schemas.ClientResponse, status_code=status.HTTP_201_CREATED)
async def create_client(client: schemas.ClientCreate, db: AsyncSession = Depends(get_db)):
    return await crud.create_client(db, client)

@router.get("/clients/{client_id}", response_model=schemas.ClientResponse)
async def read_client(client_id: int, db: AsyncSession = Depends(get_db)):
    db_client = await crud.get_client(db, client_id)
    if not db_client:
        raise HTTPException(status_code=404, detail="Client not found")
    return db_client

@router.get("/clients", response_model=List[schemas.ClientResponse])
async def read_clients(db: AsyncSession = Depends(get_db)):
    return await crud.get_clients(db)