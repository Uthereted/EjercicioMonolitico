import logging.config
import asyncio
import os
from contextlib import asynccontextmanager
from contextlib import suppress

from fastapi import FastAPI
from .routers import delivery_router
from .sql import models
from .sql import database
from . import workflow

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with database.engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
        await connection.run_sync(workflow.upgrade)

    retry_task = asyncio.create_task(workflow.retry_notifications())
    try:
        yield
    finally:
        retry_task.cancel()
        with suppress(asyncio.CancelledError):
            await retry_task
        await database.engine.dispose()


app = FastAPI(
    title="Delivery microservice",
    description="Service responsible for delivery management.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(delivery_router.router)
