"""Service-owned database configured through the shared chassis."""
import os
from pathlib import Path
from my_chassis.database import create_database

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SQLALCHEMY_DATABASE_URL = os.getenv("SQLALCHEMY_DATABASE_URL")
if SQLALCHEMY_DATABASE_URL is None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SQLALCHEMY_DATABASE_URL = f"sqlite+aiosqlite:///{(DATA_DIR / 'delivery.db').as_posix()}"
engine, SessionLocal, Base = create_database(SQLALCHEMY_DATABASE_URL, expire_on_commit=False)


async def initialize():
    """Create local tables and apply this service's schema migrations."""
    from .. import events  # Register the chassis tables in this service's metadata.
    from . import models, migrations

    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
        await conn.run_sync(migrations.upgrade)
