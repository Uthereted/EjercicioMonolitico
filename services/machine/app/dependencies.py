import os
import logging

logger = logging.getLogger(__name__)
MY_MACHINE = None

# Toma la URL de Docker si existe; si no, usa localhost para desarrollo local
ORDERS_SERVICE_URL = os.getenv("ORDERS_SERVICE_URL", "http://localhost:8000")

async def get_machine():
    global MY_MACHINE
    if MY_MACHINE is None:
        from app.async_machine import Machine
        MY_MACHINE = await Machine.create(ORDERS_SERVICE_URL)
    return MY_MACHINE