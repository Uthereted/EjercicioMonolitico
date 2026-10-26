"""React to payment, manufacturing and delivery facts using local state."""
from sqlalchemy.dialects.sqlite import insert
from .contracts import ClientCreated, ClientCatalogue, PaymentResult, PieceFact, DeliveryFact
from .events import RetryEvent
from .sql import models
from . import workflow


TOPICS = ('client.created', 'clients.catalogue.published', 'payment.approved', 'payment.rejected', 'piece.manufacturing.started', 'piece.manufactured', 'delivery.created', 'delivery.ready', 'delivery.delivered', 'auth.signing-key.published')

async def handle_event(db, event_type, payload):
    if event_type in ("client.created", "clients.catalogue.published"):
        ids = ([ClientCreated.model_validate(payload).client_id] if event_type == "client.created"
               else ClientCatalogue.model_validate(payload).client_ids)
        for client_id in ids:
            await db.execute(insert(models.KnownClient).values(id=client_id).on_conflict_do_nothing())
        return
    if event_type in ("payment.approved", "payment.rejected"):
        fact = PaymentResult.model_validate(payload)
        if fact.accepted != (event_type == "payment.approved"):
            raise ValueError("Payment outcome does not match event")
        order = await db.get(models.Order, fact.order_id)
        if order is None:
            raise ValueError("Unknown order")
        if order.client_id != fact.client_id or order.total_price != fact.amount:
            raise ValueError("Payment details do not match order")
        if order.payment_completed and not fact.accepted or order.status == "Rejected" and fact.accepted:
            raise ValueError("Conflicting payment result")
        if not order.payment_completed and order.status != "Rejected":
            order.payment_completed = fact.accepted
            order.status = "Accepted" if fact.accepted else "Rejected"
            order.integration_error = None if fact.accepted else fact.reason
            await db.flush()
        await workflow.stage_order_facts(db, order)
        return
    if event_type in ("piece.manufacturing.started", "piece.manufactured"):
        fact = PieceFact.model_validate(payload)
        piece = await db.get(models.Piece, fact.piece_id)
        if piece is None or fact.order_id is not None and piece.order_id != fact.order_id:
            raise ValueError("Unknown piece")
        order = await db.get(models.Order, piece.order_id)
        if not order.payment_completed:
            raise RetryEvent("Payment confirmation is pending")
        if piece.status != "Manufactured":
            # A finished fact is authoritative even if a started fact arrives later.
            piece.status = "Manufactured" if event_type == "piece.manufactured" else "Manufacturing"
            await db.flush()
        await workflow.stage_order_facts(db, order)
        return
    if event_type in ("delivery.created", "delivery.ready", "delivery.delivered"):
        fact = DeliveryFact.model_validate(payload)
        order = await db.get(models.Order, fact.order_id)
        if order is None or order.client_id != fact.client_id:
            raise ValueError("Delivery does not match order")
        if order.delivery_id is not None and order.delivery_id != fact.delivery_id:
            raise ValueError("Conflicting delivery")
        order.delivery_id = fact.delivery_id
        if event_type != "delivery.created" and order.status != "Delivered":
            pieces = await workflow.pieces_for_order(db, order.id)
            if not order.payment_completed or any(p.status != "Manufactured" for p in pieces):
                raise RetryEvent("Manufacturing facts are pending")
            if len(pieces) != order.number_of_pieces:
                raise ValueError("Incorrect piece count")
            order.status = "Delivered" if event_type == "delivery.delivered" else "ReadyForDelivery"
        order.integration_error = None
        return
    raise ValueError("Unsupported order fact")
