import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from .routers import delivery_router
from .sql import models
from .sql import database
from . import workflow, messaging
from .auth_verifier import fetch_public_key, listen_for_cert_updates

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with database.engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
        await connection.run_sync(workflow.upgrade)

    await fetch_public_key()
    cert_listener_task = asyncio.create_task(listen_for_cert_updates())

    consumer_task = asyncio.create_task(messaging.consume_delivery_events())
    retry_task = asyncio.create_task(workflow.retry_notifications())

    try:
        yield

    finally:
        for task in (consumer_task, retry_task, cert_listener_task):
            task.cancel()
        for task in (consumer_task, retry_task, cert_listener_task):
            with suppress(asyncio.CancelledError):
                await task
        await database.engine.dispose()


app = FastAPI(
    title="Delivery microservice",
    description="Service responsible for delivery management.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(delivery_router.router)
