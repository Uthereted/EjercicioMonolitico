from my_chassis.database import database_ready
from my_chassis.health import HealthMonitor, ResourceMonitor
from my_chassis.discovery import registered_service
import asyncio
from contextlib import asynccontextmanager
from my_chassis.events import stop_tasks
from fastapi import FastAPI, Depends
from my_chassis import create_health_router
from service_logging import install_logging, runtime
from app.dependencies import get_machine, stop_machine
from app.async_machine import Machine
from app import messaging, events
from app.sql import database
from app.auth_verifier import fetch_public_key, verify_jwt

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
    machine = await get_machine()
    publisher = asyncio.create_task(events.publish_pending_events())
    consumer = asyncio.create_task(events.consume_events("machine.domain", messaging.handle_event, topics=messaging.TOPICS))
    app.state.tasks = [publisher, consumer, *machine.tasks]
    try:
        async with health_monitor.lifespan(), resource_monitor.lifespan(), registered_service("machine-service"):
            yield
    finally:
        await stop_tasks((consumer, publisher))
        await stop_machine()
        await database.engine.dispose()


app = FastAPI(title="Machine Service", version="1.0.0", lifespan=lifespan)
app.include_router(create_health_router("machine-service", is_ready=lambda: health_monitor.available, resources=resource_monitor.snapshot), prefix="/service")
install_logging(app, 'machine')

@app.get("/machine/status")
async def get_status(machine: Machine = Depends(get_machine),
                     token_payload: dict = Depends(verify_jwt)
):
    working_piece_id = machine.working_piece['id'] if machine.working_piece else None
    return {
        "status": machine.status,
        "working_piece": working_piece_id,
        "queue": await machine.list_queued_pieces()
    }
