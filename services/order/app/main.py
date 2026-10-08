# -*- coding: utf-8 -*-
"""Main file to start FastAPI application."""
import logging.config
import asyncio
import os
from contextlib import suppress
from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.routers import main_router
from app.sql import models
from app.sql import database
from app.sql import migrations
from app import workflow

# Configure logging ################################################################################
logging.config.fileConfig(os.path.join(os.path.dirname(__file__), "logging.ini"))
logger = logging.getLogger(__name__)


# App Lifespan Test#####################################################################################
@asynccontextmanager
async def lifespan(__app: FastAPI):
    """Lifespan context manager."""
    async with database.engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
        await conn.run_sync(migrations.upgrade)
    retry_task = asyncio.create_task(workflow.retry_pending_orders())
    try:
        yield
    finally:
        retry_task.cancel()
        with suppress(asyncio.CancelledError):
            await retry_task
        await database.engine.dispose()


# OpenAPI Documentation ############################################################################
APP_VERSION = os.getenv("APP_VERSION", "2.0.0")
logger.info("Running app version %s", APP_VERSION)
DESCRIPTION = """
Order microservice.
"""

tag_metadata = [
    {
        "name": "Order",
        "description": "Endpoints to **CREATE**, **READ**, **UPDATE** or **DELETE** orders.",
    }
]

app = FastAPI(
    redoc_url=None,  # disable redoc documentation.
    title="Order Service",
    description=DESCRIPTION,
    version=APP_VERSION,
    servers=[{"url": "/", "description": "Development"}],
    license_info={
        "name": "MIT License",
        "url": "https://choosealicense.com/licenses/mit/",
    },
    openapi_tags=tag_metadata,
    lifespan=lifespan,
)

app.include_router(main_router.router)
