import asyncio
import logging.config
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from app import messaging, workflow
from app.routers import main_router
from app.sql import database, migrations, models

logging.config.fileConfig(Path(__file__).with_name("logging.ini"))


@asynccontextmanager
async def lifespan(app):
    async with database.engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
        await conn.run_sync(migrations.upgrade)
    tasks = [asyncio.create_task(messaging.publish_pending_events()),
             asyncio.create_task(messaging.consume_delivery_updates()),
             asyncio.create_task(workflow.retry_pending_orders())]
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
app.include_router(main_router.router)
