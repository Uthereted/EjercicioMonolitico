import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from my_chassis import configure_logging, create_health_router
from app import messaging, events, saga
from app.auth_verifier import fetch_public_key
from app.routers import payment_router
from app.sql import database

configure_logging("payment-service")


@asynccontextmanager
async def lifespan(app):
    await database.initialize()
    await fetch_public_key()
    tasks = [asyncio.create_task(events.publish_pending_events()),
             asyncio.create_task(events.consume_events("payment.domain", messaging.handle_event, topics=messaging.TOPICS)),
             asyncio.create_task(events.consume_events("payment.saga", saga.handle_event, topics=saga.TOPICS))]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await database.engine.dispose()


app = FastAPI(title="Payment Service", docs_url="/payment", redoc_url=None, lifespan=lifespan)
app.include_router(create_health_router("payment-service"))
app.include_router(create_health_router("payment-service"), prefix="/service")
app.include_router(payment_router.router)
