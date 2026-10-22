from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.routers import clients_router
from app.sql import database, models, crud
from app import crypto, messaging


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with database.engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)

    async with database.SessionLocal() as db:
        await crud.seed_initial_data(db)

    crypto.generate_rsa_keys()
    await messaging.notify_cert_updated()

    yield
    await database.engine.dispose()


app = FastAPI(
    title="Client & Auth Service",
    docs_url="/client",
    redoc_url=None,
    lifespan=lifespan
)

app.include_router(clients_router.router)