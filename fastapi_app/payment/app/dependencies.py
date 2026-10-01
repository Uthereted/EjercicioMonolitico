# -*- coding: utf-8 -*-
"""Application dependency injector."""
import logging

logger = logging.getLogger(__name__)

MY_MACHINE = None


# Database #########################################################################################
# -*- coding: utf-8 -*-
"""Application dependency injector."""
import logging
import os
import httpx

logger = logging.getLogger(__name__)

CLIENT_SERVICE_URL = os.getenv("CLIENT_SERVICE_URL", "http://clientservice:8000")


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


# Client service ####################################################################################
async def client_exists(client_id: int) -> bool:
    """Checks whether a client exists, calling the Client microservice's REST API."""
    async with httpx.AsyncClient(timeout=5.0) as http_client:
        try:
            response = await http_client.get(f"{CLIENT_SERVICE_URL}/client/{client_id}")
        except httpx.RequestError as exc:
            logger.error("Could not reach Client service: %s", exc)
            raise
    return response.status_code == 200