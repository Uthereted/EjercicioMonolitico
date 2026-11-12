import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from my_chassis import create_health_router
from app.routers import clients_router
from app.sql import database
from app import crypto, messaging, events


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.initialize()
    crypto.generate_rsa_keys()
    tasks = [asyncio.create_task(messaging.publish_signing_key()),
             asyncio.create_task(events.publish_pending_events())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await database.engine.dispose()


app = FastAPI(title="Client & Auth Service", docs_url="/client", redoc_url=None, lifespan=lifespan)
app.include_router(create_health_router("client-service"), prefix="/service")
app.include_router(clients_router.router)
