from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from . import models, schemas

async def create_client(db: AsyncSession, client: schemas.ClientCreate):
    db_client = models.Client(name=client.name, email=client.email)
    db.add(db_client)
    await db.commit()
    await db.refresh(db_client)
    return db_client

async def get_client(db: AsyncSession, client_id: int):
    return await db.get(models.Client, client_id)

async def get_clients(db: AsyncSession):
    result = await db.execute(select(models.Client))
    return result.scalars().all()