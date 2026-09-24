from typing import List
from fastapi import FastAPI, Depends, status
from app.dependencies import get_machine
from app.async_machine import Machine

app = FastAPI(title="Machine Service", version="1.0.0")

@app.get("/machine/status")
async def get_status(machine: Machine = Depends(get_machine)):
    working_piece_id = machine.working_piece['id'] if machine.working_piece else None
    return {
        "status": machine.status,
        "working_piece": working_piece_id,
        "queue": await machine.list_queued_pieces()
    }

@app.post("/machine/queue", status_code=status.HTTP_201_CREATED)
async def add_to_queue(piece_ids: List[int], machine: Machine = Depends(get_machine)):
    await machine.add_pieces_to_queue(piece_ids)
    return {"detail": "Pieces added"}

@app.delete("/machine/queue/{piece_id}")
async def remove_from_queue(piece_id: int, machine: Machine = Depends(get_machine)):
    removed = await machine.remove_piece_from_queue(piece_id)
    return {"removed": removed}