from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from app.dependencies import get_db
from app.sql import crud, schemas
from app.messaging import publish_client_created_event

router = APIRouter()


@router.post("/clients", response_model=schemas.ClientResponse, status_code=status.HTTP_201_CREATED)
async def create_client(
        client: schemas.ClientCreate,
        background_tasks: BackgroundTasks,
        db: AsyncSession = Depends(get_db)
):
    db_client = await crud.create_client(db, client)

    # Publicar evento de RabbitMQ en segundo plano
    client_dict = {
        "id": db_client.id,
        "name": getattr(db_client, "name", ""),
        "email": getattr(db_client, "email", "")
    }
    background_tasks.add_task(publish_client_created_event, client_dict)

    return db_client


@router.get("/clients/{client_id}", response_model=schemas.ClientResponse)
async def read_client(client_id: int, db: AsyncSession = Depends(get_db)):
    db_client = await crud.get_client(db, client_id)
    if not db_client:
        raise HTTPException(status_code=404, detail="Client not found")
    return db_client


@router.get("/clients", response_model=List[schemas.ClientResponse])
async def read_clients(db: AsyncSession = Depends(get_db)):
    return await crud.get_clients(db)