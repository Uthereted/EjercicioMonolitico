# -*- coding: utf-8 -*-
"""Entry point for the Machine microservice."""
import logging
from fastapi import FastAPI
from app.database import init_db, async_session_factory
from app.async_machine import Machine
from app.dependencies import set_machine
from app.routers import machine_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Machine Microservice")
app.include_router(machine_router.router)


@app.on_event("startup")
async def startup_event():
    """Create the tables (demo) and start the machine simulator."""
    await init_db()
    machine = await Machine.create(async_session_factory)
    await set_machine(machine)
    logger.info("Machine microservice started.")
