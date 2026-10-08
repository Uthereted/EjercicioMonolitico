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
from .sql.database import SessionLocal

logger = logging.getLogger(__name__)

CLIENT_SERVICE_URL = os.getenv("CLIENT_SERVICE_URL", "http://localhost:13005")


# Database #########################################################################################
async def get_db():
    """Generates database sessions and closes them when finished."""
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


# Client service ####################################################################################
async def client_exists(client_id: int) -> bool:
    """Checks whether a client exists, calling the Client microservice's REST API."""
    async with httpx.AsyncClient(timeout=5.0) as http_client:
        try:
            response = await http_client.get(f"{CLIENT_SERVICE_URL}/clients/{client_id}")
            if response.status_code != 404:
                response.raise_for_status()
        except httpx.RequestError as exc:
            logger.error("Could not reach Client service: %s", exc)
            raise
    return response.status_code == 200
