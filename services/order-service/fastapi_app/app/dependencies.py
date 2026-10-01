# -*- coding: utf-8 -*-

import logging

logger = logging.getLogger(__name__)


async def get_db():
    from app.sql.database import SessionLocal

    logger.debug("Getting database SessionLocal")

    db = SessionLocal()

    try:
        yield db
        await db.commit()

    except:
        await db.rollback()
        raise

    finally:
        await db.close()
