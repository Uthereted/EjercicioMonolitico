from .sql.database import SessionLocal


async def get_db():
    async with SessionLocal() as db:
        yield db
