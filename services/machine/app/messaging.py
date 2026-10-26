"""Machine subscribes to paid orders and persists its own work."""
from sqlalchemy.dialects.sqlite import insert
from .contracts import OrderPaid
from .sql.models import WorkPiece


async def handle_event(db, event_type, payload):
    if event_type != "order.paid":
        raise ValueError("Unsupported machine fact")
    fact = OrderPaid.model_validate(payload)
    for piece_id in fact.piece_ids:
        existing = await db.get(WorkPiece, piece_id)
        if existing is not None and existing.order_id not in (None, fact.order_id):
            raise ValueError("Piece belongs to another order")
        await db.execute(insert(WorkPiece).values(
            id=piece_id, order_id=fact.order_id,
            status="Manufactured" if piece_id in fact.manufactured_piece_ids else "Queued",
        ).on_conflict_do_nothing(index_elements=["id"]))
