import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from my_chassis import configure_logging, create_health_router
from app import messaging, workflow
from app.routers import main_router
from app.sql import database, migrations, models
from app.auth_verifier import fetch_public_key, listen_for_cert_updates

configure_logging("order-service")


@asynccontextmanager
async def lifespan(app):
    async with database.engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
        await conn.run_sync(migrations.upgrade)
    await fetch_public_key()
    tasks = [asyncio.create_task(messaging.publish_pending_events()),
             asyncio.create_task(messaging.consume_order_events()),
             asyncio.create_task(messaging.consume_payment_results()),
             asyncio.create_task(workflow.retry_pending_orders()),
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


app = FastAPI(title="Order Service", redoc_url=None, lifespan=lifespan)
app.include_router(create_health_router("order-service"))
app.include_router(main_router.router)
