# -*- coding: utf-8 -*-
"""FastAPI router with machine-only endpoints."""
import logging
from fastapi import APIRouter, Depends
from app.async_machine import Machine
from app.dependencies import get_machine
from app.sql import schemas

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/",
    summary="Health check endpoint",
    response_model=schemas.Message,
)
async def health_check():
    """Endpoint to check if everything started correctly."""
    logger.debug("GET '/' endpoint called.")
    return {"detail": "OK"}


@router.get(
    "/machine/status",
    summary="Retrieve machine status",
    response_model=schemas.MachineStatusResponse,
    tags=["Machine"],
)
async def machine_status(
        my_machine: Machine = Depends(get_machine)
):
    """Retrieve machine status."""
    logger.debug("GET '/machine/status' endpoint called.")
    working_piece_id = None
    if my_machine.working_piece is not None:
        working_piece_id = my_machine.working_piece["id"]

    queue = await my_machine.list_queued_pieces()

    return schemas.MachineStatusResponse(
        status=my_machine.status,
        working_piece=working_piece_id,
        queue=queue
    )
