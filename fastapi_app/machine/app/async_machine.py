import asyncio
import logging
from random import randint
import httpx

logger = logging.getLogger(__name__)

class Machine:
    STATUS_WAITING = "Waiting"
    STATUS_CHANGING_PIECE = "Changing Piece"
    STATUS_WORKING = "Working"

    def __init__(self, orders_service_url: str = "http://localhost:8000"):
        self.orders_service_url = orders_service_url
        self.__manufacturing_queue = asyncio.Queue()
        self.working_piece = None
        self.status = self.STATUS_WAITING

    @classmethod
    async def create(cls, orders_service_url: str = "http://localhost:8000"):
        self = Machine(orders_service_url)
        asyncio.create_task(self.manufacturing_coroutine())
        await self.reload_queue_from_orders_service()
        return self

    async def reload_queue_from_orders_service(self):
        """Pide las piezas en cola al servicio de órdenes vía HTTP en vez de usar crud.py[cite: 4, 7]."""
        async with httpx.AsyncClient() as client:
            try:
                res = await client.get(f"{self.orders_service_url}/piece/by-status/Queued")
                if res.status_code == 200:
                    for piece in res.json():
                        await self.__manufacturing_queue.put(piece["id"])
            except Exception as e:
                logger.error("Error conectando con órdenes: %s", e)

    async def create_piece(self, piece_id: int):
        async with httpx.AsyncClient() as client:
            # 1. Cambiar a 'Manufacturing' mediante HTTP PATCH[cite: 4]
            self.status = self.STATUS_WORKING
            self.working_piece = {"id": piece_id}
            await client.patch(
                f"{self.orders_service_url}/piece/{piece_id}", 
                json={"status": "Manufacturing"}
            )

            # 2. Simular fabricación[cite: 4]
            await asyncio.sleep(randint(5, 20))

            # 3. Cambiar a 'Manufactured' mediante HTTP PATCH[cite: 4]
            self.status = self.STATUS_CHANGING_PIECE
            await client.patch(
                f"{self.orders_service_url}/piece/{piece_id}", 
                json={"status": "Manufactured"}
            )
        self.working_piece = None

    async def manufacturing_coroutine(self):
        while True:
            if self.__manufacturing_queue.empty():
                self.status = self.STATUS_WAITING
            piece_id = await self.__manufacturing_queue.get()
            await self.create_piece(piece_id)
            self.__manufacturing_queue.task_done()

    async def add_pieces_to_queue(self, piece_ids: list):
        for p_id in piece_ids:
            await self.__manufacturing_queue.put(p_id)

    async def list_queued_pieces(self):
        return list(self.__manufacturing_queue.__dict__['_queue'])