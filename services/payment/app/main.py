import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from my_chassis import configure_logging, create_health_router
from app import messaging
from app.auth_verifier import fetch_public_key, listen_for_cert_updates
from app.routers import payment_router
from app.sql import database, models

configure_logging("payment-service")


@asynccontextmanager
async def lifespan(app):
    async with database.engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
        for index in models.Transaction.__table__.indexes:
            await conn.run_sync(lambda connection, index=index: index.create(connection, checkfirst=True))
    await fetch_public_key()
    tasks = [asyncio.create_task(messaging.consume_orders()),
             asyncio.create_task(listen_for_cert_updates())]
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
app.include_router(payment_router.router)
