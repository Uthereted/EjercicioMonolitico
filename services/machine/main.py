import asyncio
from typing import List
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI, Depends, status
from app.dependencies import get_machine, stop_machine
from app.async_machine import Machine
from app import messaging, events
from app.sql import database, models
from app.auth_verifier import fetch_public_key, verify_jwt

@asynccontextmanager
async def lifespan(app):
    async with database.engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    await fetch_public_key()
    machine = await get_machine()
    publisher = asyncio.create_task(events.publish_pending_events())
    consumer = asyncio.create_task(events.consume_events("machine.domain", messaging.handle_event))
    try:
        yield
    finally:
        for task in (consumer, publisher):
            task.cancel()
        for task in (consumer, publisher):
            with suppress(asyncio.CancelledError):
                await task
        await stop_machine()
        await database.engine.dispose()


app = FastAPI(title="Machine Service", version="1.0.0", lifespan=lifespan)

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

@app.post("/machine/queue", status_code=status.HTTP_201_CREATED)
async def add_to_queue(piece_ids: List[int], machine: Machine = Depends(get_machine), token_payload: dict = Depends(verify_jwt)):
    await machine.add_pieces_to_queue(piece_ids)
    return {"detail": "Pieces added"}

@app.delete("/machine/queue/{piece_id}")
async def remove_from_queue(piece_id: int, machine: Machine = Depends(get_machine), token_payload: dict = Depends(verify_jwt)):
    removed = await machine.remove_piece_from_queue(piece_id)
    return {"removed": removed}
