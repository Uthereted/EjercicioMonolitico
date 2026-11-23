from my_chassis.database import database_ready
from my_chassis.health import HealthMonitor
from my_chassis.discovery import registered_service, get_service_url
import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from my_chassis import create_health_router
from service_logging import install_logging, runtime
from app import messaging, events, saga
from app.auth_verifier import fetch_public_key
from app.routers import payment_router, payment_queries
from app.sql import database

health_monitor = HealthMonitor({
    'database': lambda: database_ready(database.engine),
    'rabbitmq': lambda: events.bus.available,
    'client_queries': lambda: get_service_url('client-service'),
    'logging': lambda: runtime.available,
    'public_key': fetch_public_key,
    'workers': lambda: all(not task.done() for task in app.state.tasks),
})


@asynccontextmanager
async def lifespan(app):
    await database.initialize()
    await fetch_public_key()
    tasks = [asyncio.create_task(events.publish_pending_events()),
             asyncio.create_task(events.consume_events("payment.domain", messaging.handle_event, topics=messaging.TOPICS)),
             asyncio.create_task(events.consume_events("payment.saga", saga.handle_event, topics=saga.TOPICS))]
    app.state.tasks = tasks
    try:
        async with health_monitor.lifespan(), registered_service("payment-service"):
            yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await database.engine.dispose()


app = FastAPI(title="Payment Service", docs_url="/payment", redoc_url=None, lifespan=lifespan)
app.include_router(create_health_router("payment-service", is_ready=lambda: health_monitor.available))
app.include_router(create_health_router("payment-service", is_ready=lambda: health_monitor.available), prefix="/service")
app.include_router(payment_router.router)
app.include_router(payment_queries.router)
install_logging(app, 'payments')
