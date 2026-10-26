import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from app.routers import clients_router
from app.sql import database, models, crud
from app import crypto, messaging, events


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with database.engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    async with database.SessionLocal() as db:
        await crud.seed_initial_data(db)
    crypto.generate_rsa_keys()
    tasks = [asyncio.create_task(messaging.publish_catalogue()),
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
app.include_router(clients_router.router)
