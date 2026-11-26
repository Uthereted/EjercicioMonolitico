"""Order creation command; query handlers are in order_queries.py."""
from my_chassis.auth import get_bearer_token
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.dependencies import get_db
from app.sql import models, schemas
from app import saga, workflow
from app.auth_verifier import verify_jwt

router = APIRouter()


@router.post("/order", response_model=schemas.Order, status_code=202, tags=["Order"])
async def create_order(order_schema: schemas.OrderPost,
                       db: AsyncSession = Depends(get_db),
                       token_payload: dict = Depends(verify_jwt),
                       access_token: str = Depends(get_bearer_token)):
    if "admin" not in token_payload.get("roles", []) and token_payload.get("client_id") != order_schema.client_id:
        raise HTTPException(403, "Forbidden: el pedido debe pertenecer a tu cliente")
    client = await workflow.validate_client(order_schema.client_id, access_token)
    address = order_schema.address
    if address is None:
        if not client.address or not client.postal_code:
            raise HTTPException(409, "Client address is missing")
        address = f"{client.address}, {client.postal_code}"
        if len(address) > 500:
            raise HTTPException(422, "Delivery address including postal code must not exceed 500 characters")
    order = models.Order(
        **order_schema.model_dump(exclude={"address"}), address=address, unit_price=workflow.UNIT_PRICE,
        total_price=order_schema.number_of_pieces * workflow.UNIT_PRICE,
        status="PendingPayment",
    )
    db.add(order)
    await db.flush()
    db.add_all([models.Piece(order_id=order.id, status="Queued")
                for _ in range(order.number_of_pieces)])
    await db.flush()
    await saga.start(db, order)
    await db.commit()
    await db.refresh(order)
    return order
