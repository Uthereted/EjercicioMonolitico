import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from .routers import delivery_router
from .sql import database
from . import messaging, events, saga
from .auth_verifier import fetch_public_key


@asynccontextmanager
async def lifespan(app):
    await database.initialize()
    await fetch_public_key()
    tasks = [asyncio.create_task(events.publish_pending_events()),
             asyncio.create_task(events.consume_events("delivery.domain", messaging.handle_event, topics=messaging.TOPICS)),
             asyncio.create_task(events.consume_events("delivery.saga", saga.handle_event, topics=saga.TOPICS))]
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
