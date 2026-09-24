import asyncio
import logging
from random import randint
import httpx

logger = logging.getLogger(__name__)


class Machine:
    """Piece manufacturing machine simulator using HTTP to communicate with Orders Service."""
    STATUS_WAITING = "Waiting"
    STATUS_CHANGING_PIECE = "Changing Piece"
    STATUS_WORKING = "Working"
    __manufacturing_queue = asyncio.Queue()
    __stop_machine = False
    working_piece = None
    status = STATUS_WAITING

    def __init__(self, orders_service_url: str = "http://localhost:8000"):
        self.orders_service_url = orders_service_url

    @classmethod
    async def create(cls, orders_service_url: str = "http://localhost:8000"):
        """Machine constructor: loads manufacturing/queued pieces and starts simulation."""
        logger.info("AsyncMachine microservice initialized")
        self = Machine(orders_service_url)
        asyncio.create_task(self.manufacturing_coroutine())
        await self.reload_queue_from_orders_service()
        return self

    async def reload_queue_from_orders_service(self):
        """Reload queue from Orders Service via HTTP when rebooted."""
        async with httpx.AsyncClient() as client:
            try:
                # 1. Recuperar pieza que se estaba fabricando antes de reiniciar
                res_m = await client.get(f"{self.orders_service_url}/piece/by-status/Manufacturing")
                if res_m.status_code == 200 and res_m.json():
                    await self.add_piece_to_queue_by_id(res_m.json()[0]['id'])

                # 2. Recuperar piezas en cola
                res_q = await client.get(f"{self.orders_service_url}/piece/by-status/Queued")
                if res_q.status_code == 200 and res_q.json():
                    for piece in res_q.json():
                        await self.add_piece_to_queue_by_id(piece['id'])
            except Exception as exc:
                logger.error("Error reloading queue from Orders Service: %s", exc)

    async def manufacturing_coroutine(self) -> None:
        """Coroutine that manufactures queued pieces one by one."""
        while not self.__stop_machine:
            if self.__manufacturing_queue.empty():
                self.status = self.STATUS_WAITING
            piece_id = await self.__manufacturing_queue.get()
            await self.create_piece(piece_id)
            self.__manufacturing_queue.task_done()

    async def create_piece(self, piece_id: int):
        """Simulates piece manufacturing and updates Orders Service over HTTP."""
        async with httpx.AsyncClient() as client:
            # Notificar inicio de fabricación al servicio principal
            self.status = self.STATUS_WORKING
            self.working_piece = {"id": piece_id}
            await client.patch(
                f"{self.orders_service_url}/piece/{piece_id}",
                json={"status": "Manufacturing"}
            )

        await asyncio.sleep(randint(5, 20))  # Simula el tiempo de fabricación

        async with httpx.AsyncClient() as client:
            # Notificar fin de fabricación al servicio principal
            self.status = self.STATUS_CHANGING_PIECE
            res = await client.patch(
                f"{self.orders_service_url}/piece/{piece_id}",
                json={"status": "Manufactured"}
            )
            if res.status_code == 200:
                self.working_piece = res.json()

        self.working_piece = None

    async def add_pieces_to_queue(self, piece_ids: list):
        """Adds a list of piece IDs to the queue."""
        logger.debug("Adding %i pieces to queue", len(piece_ids))
        for p_id in piece_ids:
            await self.add_piece_to_queue_by_id(p_id)

    async def add_piece_to_queue_by_id(self, piece_id: int):
        """Adds a single piece ID to the queue."""
        await self.__manufacturing_queue.put(piece_id)

    async def remove_piece_from_queue(self, piece_id: int) -> bool:
        """Removes the given piece ID from the queue."""
        logger.info("Removing piece %i", piece_id)
        if self.working_piece and self.working_piece.get('id') == piece_id:
            logger.warning("Piece %i is being manufactured, cannot remove from queue", piece_id)
            return False

        item_list = []
        removed = False
        while not self.__manufacturing_queue.empty():
            item_list.append(self.__manufacturing_queue.get_nowait())

        for item in item_list:
            if item != piece_id:
                self.__manufacturing_queue.put_nowait(item)
            else:
                logger.debug("Piece %i removed from queue.", piece_id)
                removed = True

        if not removed:
            logger.warning("Piece %i not found in queue.", piece_id)

        return removed

    async def list_queued_pieces(self):
        """Get queued piece ids as list."""
        return list(self.__manufacturing_queue.__dict__['_queue'])