# -*- coding: utf-8 -*-
"""Database engine and session configuration."""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

# SQLite async, para poder levantar el microservicio sin montar Postgres.
# Cambia esta URL si quieres usar otra base de datos (p.ej. postgresql+asyncpg://...).
DATABASE_URL = "sqlite+aiosqlite:///./machine_service.db"

engine = create_async_engine(DATABASE_URL, echo=False)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()


async def init_db():
    """Create tables on startup (suficiente para este microservicio mínimo)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
