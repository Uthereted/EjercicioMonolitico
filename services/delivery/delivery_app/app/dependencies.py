# -*- coding: utf-8 -*-
"""Application dependency injector."""
from .sql.database import SessionLocal


async def get_db():
    """Generates database sessions and closes them when finished."""
    db = SessionLocal()
    try:
        yield db
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()