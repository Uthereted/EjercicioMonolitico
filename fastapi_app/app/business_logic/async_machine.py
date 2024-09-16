# -*- coding: utf-8 -*-
"""Simulation of a machine that manufactures pieces."""
import asyncio
import logging
from random import randint
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import ProgrammingError, OperationalError
from app.sql import crud
from app.sql.models import Piece, Order
from app.sql.database import SessionLocal

logger = logging.getLogger(__name__)
logger.debug("Machine logger set.")


class Machine:
    """Piece manufacturing machine simulator."""
    STATUS_WAITING = "Waiting"
    STATUS_CHANGING_PIECE = "Changing Piece"
    STATUS_WORKING = "Working"
    __manufacturing_queue = asyncio.Queue()
    __stop_machine = False
    working_piece = None
    status = STATUS_WAITING

    @classmethod
    async def create(cls):
        """Machine constructor: loads manufacturing/queued pieces and starts simulation."""
        logger.info("AsyncMachine initialized")
        self = Machine()
        asyncio.create_task(self.manufacturing_coroutine())
        await self.reload_queue_from_database()
        return self

    async def reload_queue_from_database(self):
        """Reload queue from database, to reload data when the system has been rebooted."""
        # Load the piece that was being manufactured
        async with SessionLocal() as db:
            manufacturing_piece = await Machine.get_manufacturing_piece(db)
            if manufacturing_piece:
                await self.add_piece_to_queue(manufacturing_piece)

            # Load the pieces that were in the queue
            queued_pieces = await Machine.get_queued_pieces(db)

            if queued_pieces:
                await self.add_pieces_to_queue(queued_pieces)
            await db.close()

    @staticmethod
    async def get_manufacturing_piece(db: AsyncSession):
        """Gets the manufacturing piece from the database."""
        try:
            manufacturing_pieces = await crud.get_piece_list_by_status(
                db,
                Piece.STATUS_MANUFACTURING
            )
            if manufacturing_pieces and manufacturing_pieces[0]:
                return manufacturing_pieces[0]
        except (ProgrammingError, OperationalError):
            logger.error(
                "Error getting Manufacturing Piece at startup. It may be the first execution"
            )
        return None

    @staticmethod
    async def get_queued_pieces(db: AsyncSession):
        """Get all queued pieces from the database."""
        try:
            queued_pieces = await crud.get_piece_list_by_status(db, Piece.STATUS_QUEUED)
            return queued_pieces
        except (ProgrammingError, OperationalError):
            logger.error("Error getting Queued Pieces at startup. It may be the first execution")
            return []

    async def manufacturing_coroutine(self) -> None:
        """Coroutine that manufactures queued pieces one by one."""
        while not self.__stop_machine:
            if self.__manufacturing_queue.empty():
                self.status = self.STATUS_WAITING
            piece_id = await self.__manufacturing_queue.get()
            await self.create_piece(piece_id)
            self.__manufacturing_queue.task_done()

    async def create_piece(self, piece_id: int):
        """Simulates piece manufacturing."""
        # Machine and piece status updated during manufacturing
        async with SessionLocal() as db:
            await self.update_working_piece(piece_id, db)
            await self.working_piece_to_manufacturing(db)  # Update Machine&piece status
            await db.close()

        await asyncio.sleep(randint(5, 20))  # Simulates time spent manufacturing

        async with SessionLocal() as db:
            await self.working_piece_to_finished(db)  # Update Machine&Piece status
            await db.close()

        self.working_piece = None

    async def update_working_piece(self, piece_id: int, db: AsyncSession):
        """Loads a piece for the given id and updates the working piece."""
        logger.debug("Updating working piece to %i", piece_id)
        piece = await crud.get_piece(db, piece_id)
        self.working_piece = piece.as_dict()

    async def working_piece_to_manufacturing(self, db: AsyncSession):
        """Updates piece status to manufacturing."""
        self.status = Machine.STATUS_WORKING
        try:
            await crud.update_piece_status(db, self.working_piece['id'], Piece.STATUS_MANUFACTURING)
        except Exception as exc:  # @ToDo: To general exception
            logger.error("Could not update working piece status to manufacturing: %s", exc)

    async def working_piece_to_finished(self, db: AsyncSession):
        """Updates piece status to finished and order if all pieces are finished."""
        logger.debug("Working piece finished.")
        self.status = Machine.STATUS_CHANGING_PIECE

        piece = await crud.update_piece_status(
            db,
            self.working_piece['id'],
            Piece.STATUS_MANUFACTURED
        )
        self.working_piece = piece.as_dict()

        piece = await crud.update_piece_manufacturing_date_to_now(
            db,
            self.working_piece['id']
        )
        self.working_piece = piece.as_dict()

        if await Machine.is_order_finished(self.working_piece['order_id'], db):
            await crud.update_order_status(
                db,
                self.working_piece['order_id'],
                Order.STATUS_FINISHED
            )

    @staticmethod
    async def is_order_finished(order_id, db: AsyncSession):
        """Return whether an order is finished or not."""
        db_order = await crud.get_order(db, order_id)
        if not db_order:  # Just in case order has been removed
            return False

        for piece in db_order.pieces:
            if piece.status != Piece.STATUS_MANUFACTURED:
                return False

        return True

    async def add_pieces_to_queue(self, pieces):
        """Adds a list of pieces to the queue and updates their status."""
        logger.debug("Adding %i pieces to queue", len(pieces))
        for piece in pieces:
            await self.add_piece_to_queue(piece)

    async def add_piece_to_queue(self, piece):
        """Adds the given piece from the queue."""
        await self.__manufacturing_queue.put(piece.id)

    async def remove_pieces_from_queue(self, pieces):
        """Adds a list of pieces to the queue and updates their status."""
        logger.debug("Removing %i pieces from queue", len(pieces))
        for piece in pieces:
            await self.remove_piece_from_queue(piece)

    async def remove_piece_from_queue(self, piece) -> bool:
        """Removes the given piece from the queue."""
        logger.info("Removing piece %i", piece.id)
        if self.working_piece == piece.id:
            logger.warning(
                "Piece %i is being manufactured, cannot remove from queue\n\n",
                piece.id
            )
            return False

        item_list = []
        removed = False
        # Empty the list
        while not self.__manufacturing_queue.empty():
            item_list.append(self.__manufacturing_queue.get_nowait())

        # Fill the list with all items but *piece_id*
        for item in item_list:
            if item != piece.id:
                self.__manufacturing_queue.put_nowait(item)
            else:
                logging.debug("Piece %i removed from queue.", piece.id)
                removed = True

        if not removed:
            logger.warning("Piece %i not found in the queue.", piece.id)

        return removed

    async def list_queued_pieces(self):
        """Get queued piece ids as list."""
        piece_list = list(self.__manufacturing_queue.__dict__['_queue'])
        return piece_list
