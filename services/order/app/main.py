from my_chassis.database import database_ready
from my_chassis.health import HealthMonitor, ResourceMonitor, mongodb_ready
from my_chassis.discovery import registered_service, get_service_url
import asyncio
from contextlib import asynccontextmanager
from my_chassis.events import stop_tasks
from fastapi import FastAPI
from my_chassis import create_health_router
from service_logging import install_logging, runtime
from app import messaging, workflow, events, saga, saga_history
from app.routers import main_router, order_queries
from app.sql import database
from app.auth_verifier import fetch_public_key

resource_monitor = ResourceMonitor()

health_monitor = HealthMonitor({
    'database': lambda: database_ready(database.engine),
    'rabbitmq': lambda: events.bus.available,
    'client_queries': lambda: get_service_url('client-service'),
    'logging': lambda: runtime.available,
    'public_key': fetch_public_key,
    'workers': lambda: all(not task.done() for task in app.state.tasks),
    'history_database': lambda: mongodb_ready(saga_history.mongo),
})


@asynccontextmanager
async def lifespan(app):
    await database.initialize()
    await runtime.wait_available('Restoring order workflow')
    await workflow.bootstrap()
    await fetch_public_key()
    tasks = [asyncio.create_task(events.publish_pending_events()),
             asyncio.create_task(events.consume_events("order.domain", messaging.handle_event, topics=messaging.TOPICS)),
             asyncio.create_task(events.consume_events("order.saga", saga.handle_event, topics=saga.TOPICS))]
    app.state.tasks = tasks
    try:
        async with health_monitor.lifespan(), resource_monitor.lifespan(), registered_service("order-service"):
            yield
    finally:
        await stop_tasks(tasks)
        await database.engine.dispose()
        saga_history.mongo.close()


app = FastAPI(title="Order Service", redoc_url=None, lifespan=lifespan)
health_router = create_health_router(
    "order-service", is_ready=lambda: health_monitor.available,
    resources=resource_monitor.snapshot,
)
app.include_router(health_router)
app.include_router(health_router, prefix="/service")
app.include_router(saga_history.router)
app.include_router(main_router.router)
app.include_router(order_queries.router)
install_logging(app, 'orders')
