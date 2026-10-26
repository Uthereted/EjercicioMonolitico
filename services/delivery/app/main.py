import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from .routers import delivery_router
from .sql import models, database
from . import workflow, messaging, events
from .auth_verifier import fetch_public_key


@asynccontextmanager
async def lifespan(app):
    async with database.engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
        await conn.run_sync(workflow.upgrade)
    await fetch_public_key()
    tasks = [asyncio.create_task(events.publish_pending_events()),
             asyncio.create_task(events.consume_events("delivery.domain", messaging.handle_event, topics=messaging.TOPICS))]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await database.engine.dispose()


app = FastAPI(title="Delivery microservice", version="1.0.0", lifespan=lifespan)
app.include_router(delivery_router.router)
