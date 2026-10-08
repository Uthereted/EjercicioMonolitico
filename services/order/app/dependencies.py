# -*- coding: utf-8 -*-

import logging
from .sql.database import SessionLocal

logger = logging.getLogger(__name__)


async def get_db():
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
