# -*- coding: utf-8 -*-
"""Shared FastAPI dependencies."""
from typing import Optional
from app.database import async_session_factory
from app.async_machine import Machine

_machine_instance: Optional[Machine] = None


async def get_db():
    """Yield a database session per request."""
    async with async_session_factory() as session:
        yield session


async def get_machine() -> Machine:
    """Return the singleton Machine instance used by the whole app."""
    return _machine_instance


async def set_machine(machine: Machine):
    """Set the singleton Machine instance (called once on startup)."""
    global _machine_instance
    _machine_instance = machine
