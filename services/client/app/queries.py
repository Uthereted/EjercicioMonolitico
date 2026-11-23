"""Client read model; these queries never modify data."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .sql import models, schemas


async def get_client_profiles(db: AsyncSession, *, client_id=None):
    query = select(models.Client, models.User.username).outerjoin(
        models.User, models.User.email == models.Client.email).where(models.Client.deleted.is_(False))
    if client_id is not None:
        query = query.where(models.Client.id == client_id)
    rows = (await db.execute(query)).all()
    return [schemas.ClientResponse(
        **schemas.ClientResponse.model_validate(client).model_dump(exclude={'username'}), username=username,
    ) for client, username in rows]
