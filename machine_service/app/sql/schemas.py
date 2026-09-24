# -*- coding: utf-8 -*-
"""Pydantic schemas exposed by the machine microservice."""
from typing import List, Optional
from pydantic import BaseModel


class Message(BaseModel):
    """Generic message response (health check)."""
    detail: str


class MachineStatusResponse(BaseModel):
    """Response schema for GET /machine/status."""
    status: str
    working_piece: Optional[int] = None
    queue: List[int] = []
