from my_chassis.database import create_db_dependency
from .sql import database, models

get_db = create_db_dependency(database.SessionLocal)


async def client_exists(client_id: int) -> bool:
    async with database.SessionLocal() as db:
        return await db.get(models.KnownClient, client_id) is not None
