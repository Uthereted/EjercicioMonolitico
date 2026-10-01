from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.routers import clients_router
from app.sql import database, models

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with database.engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    yield
    await database.engine.dispose()

# Configuración personalizada de la ruta de Swagger UI
app = FastAPI(
    title="Client Service",
    docs_url="/client",
    redoc_url=None,
    lifespan=lifespan
)

app.include_router(clients_router.router)