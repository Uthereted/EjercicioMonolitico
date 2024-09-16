# -*- coding: utf-8 -*-
"""Application dependency injector."""
import logging

logger = logging.getLogger(__name__)

MY_MACHINE = None


# Database #########################################################################################
async def get_db():
    """Generates database sessions and closes them when finished."""
    from app.sql.database import SessionLocal  # pylint: disable=import-outside-toplevel
    logger.debug("Getting database SessionLocal")
    db = SessionLocal()
    try:
        yield db
        await db.commit()
    except:
        await db.rollback()
    finally:
        await db.close()


# Machine #########################################################################################
async def get_machine():
    """Returns the machine object (creates it the first time its executed)."""
    logger.debug("Getting machine")
    global MY_MACHINE
    if MY_MACHINE is None:
        from app.business_logic.async_machine import Machine
        MY_MACHINE = await Machine.create()
    return MY_MACHINE


# asyncio.create_task(get_machine())
# asyncio.run(init_machine())
