# -*- coding: utf-8 -*-
"""Main file to start FastAPI application."""
import logging.config
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.routers import payment_router
from app.sql import models
from app.sql import database

# Configure logging ################################################################################
logging.config.fileConfig(os.path.join(os.path.dirname(__file__), "logging.ini"))
logger = logging.getLogger(__name__)


# App Lifespan #####################################################################################
@asynccontextmanager
async def lifespan(__app: FastAPI):
    """Lifespan context manager."""
    try:
        logger.info("Starting up")
        async with database.engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)
            for index in models.Transaction.__table__.indexes:
                await conn.run_sync(lambda connection, index=index: index.create(connection, checkfirst=True))
        yield
    finally:
        logger.info("Shutting down database")
        await database.engine.dispose()


# OpenAPI Documentation ############################################################################
APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
logger.info("Running app version %s", APP_VERSION)
DESCRIPTION = """
Payment microservice. Manages client account balances and transactions
(deposits and order charges).
"""

tag_metadata = [
    {
        "name": "Payment",
        "description": "Endpoints to deposit funds, charge orders and check balance.",
    },
]

app = FastAPI(
    redoc_url=None,
    title="FastAPI - Payment Service",
    docs_url="/payment",
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

app.include_router(payment_router.router)
