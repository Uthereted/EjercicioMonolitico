"""One simulator instance per process, also created at application startup."""
import asyncio
import os
from app.async_machine import Machine

MY_MACHINE = None
lock = asyncio.Lock()
ORDERS_SERVICE_URL = os.getenv("ORDERS_SERVICE_URL", "http://localhost:13001")


async def get_machine():
    global MY_MACHINE
    async with lock:
        if MY_MACHINE is None:
            MY_MACHINE = await Machine.create(ORDERS_SERVICE_URL)
    return MY_MACHINE


async def stop_machine():
    global MY_MACHINE
    if MY_MACHINE is not None:
        await MY_MACHINE.close()
        MY_MACHINE = None
