"""A durable local work list replaces polling another microservice."""
import asyncio
import logging
import os
from contextlib import suppress
from random import randint
from sqlalchemy import select
from service_logging import runtime
from .events import record_event
from .sql import database
from .sql.models import WorkPiece

logger = logging.getLogger(__name__)


class Machine:
    def __init__(self):
        self.status = "Waiting"
        self.working_piece = None
        self.tasks = []

    @classmethod
    async def create(cls):
        machine = cls()
        machine.tasks = [asyncio.create_task(machine.manufacturing_coroutine())]
        return machine

    async def close(self):
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def manufacture_one(self):
        await runtime.wait_available()
        async with database.SessionLocal() as db:
            piece = await db.scalar(select(WorkPiece).where(
                WorkPiece.status.in_(["Queued", "Manufacturing"])).order_by(WorkPiece.id).limit(1))
            if piece is None:
                return False
            piece_id, order_id = piece.id, piece.order_id
            self.working_piece = {"id": piece_id}
            self.status = "Working"
            piece.status = "Manufacturing"
            await record_event(db, "piece.manufacturing.started",
                               {"piece_id": piece_id, "order_id": order_id},
                               f"piece.manufacturing.started:{piece_id}")
            await db.commit()
        # Cancellation leaves durable work in Manufacturing; startup resumes it.
        await asyncio.sleep(float(os.getenv("MANUFACTURING_SECONDS", str(randint(5, 20)))))
        await runtime.wait_available('Completing manufactured piece')
        async with database.SessionLocal() as db:
            piece = await db.get(WorkPiece, piece_id)
            piece.status = "Manufactured"
            await record_event(db, "piece.manufactured", {"piece_id": piece_id, "order_id": order_id},
                               f"piece.manufactured:{piece_id}")
            await db.commit()
        self.working_piece = None
        self.status = "Waiting"
        return True

    async def manufacturing_coroutine(self):
        while True:
            try:
                if not await self.manufacture_one():
                    await asyncio.sleep(0.25)
            except Exception:
                logger.exception("Local manufacturing will retry")
                await asyncio.sleep(1)


    async def list_queued_pieces(self):
        async with database.SessionLocal() as db:
            return list((await db.scalars(select(WorkPiece.id).where(
                WorkPiece.status == "Queued").order_by(WorkPiece.id))).all())
