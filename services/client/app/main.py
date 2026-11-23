from my_chassis.database import database_ready
from my_chassis.health import HealthMonitor, ResourceMonitor
from my_chassis.discovery import registered_service
import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from my_chassis import create_health_router
from service_logging import install_logging, runtime
from app.routers import clients_router, client_queries
from app.sql import database
from app import crypto, messaging, events
from app.auth_verifier import verifier


resource_monitor = ResourceMonitor()

health_monitor = HealthMonitor({
    'database': lambda: database_ready(database.engine),
    'rabbitmq': lambda: events.bus.available,
    'logging': lambda: runtime.available,
    'public_key': verifier.fetch_public_key,
    'workers': lambda: all(not task.done() for task in app.state.tasks),
})


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.initialize()
    crypto.generate_rsa_keys()
    tasks = [asyncio.create_task(messaging.publish_signing_key()),
             asyncio.create_task(events.publish_pending_events())]
    app.state.tasks = tasks
    try:
        async with health_monitor.lifespan(), resource_monitor.lifespan(), registered_service("client-service"):
            yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await database.engine.dispose()


app = FastAPI(title="Client & Auth Service", docs_url="/client", redoc_url=None, lifespan=lifespan)
app.include_router(create_health_router("client-service", is_ready=lambda: health_monitor.available, resources=resource_monitor.snapshot), prefix="/service")
app.include_router(clients_router.router)
app.include_router(client_queries.router)
install_logging(app, 'clients')
