import logging
logger = logging.getLogger(__name__)
MY_MACHINE = None

async def get_machine():
    global MY_MACHINE
    if MY_MACHINE is None:
        from app.async_machine import Machine
        MY_MACHINE = await Machine.create("http://localhost:8000")
    return MY_MACHINE