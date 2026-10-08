"""Manufacturing simulator with durable order state and REST callbacks."""
import asyncio
import logging
import os
from contextlib import suppress
from random import randint
import httpx

logger = logging.getLogger(__name__)


class Machine:
    STATUS_WAITING = "Waiting"
    STATUS_CHANGING_PIECE = "Changing Piece"
    STATUS_WORKING = "Working"

    def __init__(self, orders_service_url):
        self.orders_service_url = orders_service_url.rstrip("/")
        self.queue = asyncio.Queue()
        self.pending_ids = set()
        self.working_piece = None
        self.status = self.STATUS_WAITING
        self.retry_seconds = float(os.getenv("REST_RETRY_SECONDS", "5"))
        self.tasks = []

    @classmethod
    async def create(cls, orders_service_url="http://localhost:13001"):
        machine = cls(orders_service_url)
        machine.tasks = [asyncio.create_task(machine.manufacturing_coroutine()),
                         asyncio.create_task(machine.recovery_coroutine())]
        return machine

    async def close(self):
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def reload_queue_from_orders_service(self):
        async with httpx.AsyncClient(timeout=5) as client:
            for status in ("Manufacturing", "Queued"):
                response = await client.get(f"{self.orders_service_url}/piece/by-status/{status}")
                response.raise_for_status()
                await self.add_pieces_to_queue([piece["id"] for piece in response.json()])

    async def recovery_coroutine(self):
        while True:
            try:
                await self.reload_queue_from_orders_service()
            except (httpx.RequestError, httpx.HTTPStatusError):
                logger.warning("Order service unavailable; queue recovery will retry")
            await asyncio.sleep(self.retry_seconds)

    async def manufacturing_coroutine(self):
        while True:
            piece_id = await self.queue.get()
            self.working_piece = {"id": piece_id}
            manufactured = False
            try:
                while True:
                    try:
                        async with httpx.AsyncClient(timeout=5) as client:
                            if not manufactured:
                                response = await client.patch(
                                    f"{self.orders_service_url}/piece/{piece_id}",
                                    json={"status": "Manufacturing"},
                                )
                                if response.status_code in (404, 409):
                                    logger.warning("Piece %s cannot be manufactured", piece_id)
                                    break
                                response.raise_for_status()
                                if response.json()["status"] == "Manufactured":
                                    break
                                self.status = self.STATUS_WORKING
                                delay = float(os.getenv("MANUFACTURING_SECONDS", str(randint(5, 20))))
                                await asyncio.sleep(delay)
                                manufactured = True
                            self.status = self.STATUS_CHANGING_PIECE
                            response = await client.patch(
                                f"{self.orders_service_url}/piece/{piece_id}",
                                json={"status": "Manufactured"},
                            )
                            response.raise_for_status()
                        break
                    except (httpx.RequestError, httpx.HTTPStatusError):
                        self.status = "PendingNotification"
                        logger.warning("Manufacturing notification pending for piece %s", piece_id)
                        await asyncio.sleep(self.retry_seconds)
            finally:
                self.pending_ids.discard(piece_id)
                self.working_piece = None
                self.status = self.STATUS_WAITING
                self.queue.task_done()

    async def add_pieces_to_queue(self, piece_ids):
        for piece_id in piece_ids:
            if piece_id not in self.pending_ids:
                self.pending_ids.add(piece_id)
                self.queue.put_nowait(piece_id)

    async def add_piece_to_queue_by_id(self, piece_id):
        await self.add_pieces_to_queue([piece_id])

    async def remove_piece_from_queue(self, piece_id):
        if self.working_piece and self.working_piece["id"] == piece_id:
            return False
        retained = []
        removed = False
        while not self.queue.empty():
            item = self.queue.get_nowait()
            self.queue.task_done()
            if item == piece_id:
                self.pending_ids.discard(item)
                removed = True
            else:
                retained.append(item)
        for item in retained:
            self.queue.put_nowait(item)
        return removed

    async def list_queued_pieces(self):
        return list(self.queue._queue)
