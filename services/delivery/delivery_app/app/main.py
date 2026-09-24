import logging.config
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from .routers import delivery_router
from .sql import models
from .sql import database

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with database.engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)

    yield

    await database.engine.dispose()


app = FastAPI(
    title="Delivery microservice",
    description="Service responsible for delivery management.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(delivery_router.router)