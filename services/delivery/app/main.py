from my_chassis.database import database_ready
from my_chassis.health import HealthMonitor, ResourceMonitor
from my_chassis.discovery import registered_service
import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from my_chassis import create_health_router
from service_logging import install_logging, runtime
from .routers import delivery_router
from .sql import database
from . import messaging, events, saga
from .auth_verifier import fetch_public_key


resource_monitor = ResourceMonitor()

health_monitor = HealthMonitor({
    'database': lambda: database_ready(database.engine),
    'rabbitmq': lambda: events.bus.available,
    'logging': lambda: runtime.available,
    'public_key': fetch_public_key,
    'workers': lambda: all(not task.done() for task in app.state.tasks),
})


@asynccontextmanager
async def lifespan(app):
    await database.initialize()
    await fetch_public_key()
    tasks = [asyncio.create_task(events.publish_pending_events()),
             asyncio.create_task(events.consume_events("delivery.domain", messaging.handle_event, topics=messaging.TOPICS)),
             asyncio.create_task(events.consume_events("delivery.saga", saga.handle_event, topics=saga.TOPICS))]
    app.state.tasks = tasks
    try:
        async with health_monitor.lifespan(), resource_monitor.lifespan(), registered_service("delivery-service"):
            yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await database.engine.dispose()


app = FastAPI(title="Delivery microservice", version="1.0.0", lifespan=lifespan)
app.state.health_monitor = health_monitor
app.state.resource_monitor = resource_monitor
app.include_router(create_health_router("delivery-service", is_ready=lambda: health_monitor.available, resources=resource_monitor.snapshot), prefix="/service")
app.include_router(delivery_router.router)
install_logging(app, 'deliveries')
