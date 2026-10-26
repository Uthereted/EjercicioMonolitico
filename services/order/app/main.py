import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from my_chassis import configure_logging, create_health_router
from app import messaging, workflow, events
from app.routers import main_router
from app.sql import database, migrations, models
from app.auth_verifier import fetch_public_key

configure_logging("order-service")


@asynccontextmanager
async def lifespan(app):
    async with database.engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
        await conn.run_sync(migrations.upgrade)
    await workflow.bootstrap()
    await fetch_public_key()
    tasks = [asyncio.create_task(events.publish_pending_events()),
             asyncio.create_task(events.consume_events("order.domain", messaging.handle_event, topics=messaging.TOPICS))]
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
