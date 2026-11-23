"""Event contracts consumed by this service; no peer implementation imports."""
from typing import Annotated, Literal
from pydantic import BaseModel, Field

PositiveID = Annotated[int, Field(gt=0, strict=True)]


class ClientCreated(BaseModel):
    client_id: PositiveID


class OrderCreated(BaseModel):
    order_id: PositiveID
    client_id: PositiveID
    amount: PositiveID


class ReservePayment(OrderCreated):
    saga_id: PositiveID
    # Default also accepts commands queued before response routing was explicit.
    response_exchange: Literal["manufacturing.saga.responses"] = "manufacturing.saga.responses"
