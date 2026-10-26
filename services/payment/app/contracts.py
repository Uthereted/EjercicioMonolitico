"""Event contracts consumed by this service; no peer implementation imports."""
from typing import Annotated
from pydantic import BaseModel, Field, model_validator

PositiveID = Annotated[int, Field(gt=0, strict=True)]


class ClientCreated(BaseModel):
    client_id: PositiveID


class ClientCatalogue(BaseModel):
    client_ids: list[PositiveID]


class OrderCreated(BaseModel):
    order_id: PositiveID
    client_id: PositiveID
    amount: PositiveID


class PaymentResult(OrderCreated):
    accepted: bool = Field(strict=True)
    reason: str = ""
