"""One durable simulator instance per process."""
import asyncio
from app.async_machine import Machine

MY_MACHINE = None
lock = asyncio.Lock()


async def get_machine():
    global MY_MACHINE
    async with lock:
        if MY_MACHINE is None:
            MY_MACHINE = await Machine.create()
    return MY_MACHINE


async def stop_machine():
    global MY_MACHINE
    if MY_MACHINE is not None:
        await MY_MACHINE.close()
        MY_MACHINE = None
