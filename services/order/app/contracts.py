"""Event contracts consumed by this service; no peer implementation imports."""
from typing import Annotated
from pydantic import BaseModel, Field, model_validator

PositiveID = Annotated[int, Field(gt=0, strict=True)]


class ClientCreated(BaseModel):
    client_id: PositiveID
    address: str | None = Field(default=None, min_length=1, max_length=500)
    postal_code: str | None = Field(default=None, pattern=r"^[0-9]{5}$")


class ClientCatalogue(BaseModel):
    client_ids: list[PositiveID]
    clients: list[ClientCreated] = Field(default_factory=list)


class OrderCreated(BaseModel):
    order_id: PositiveID
    client_id: PositiveID
    amount: PositiveID


class PaymentResult(OrderCreated):
    accepted: bool = Field(strict=True)
    reason: str = ""


class PieceFact(BaseModel):
    piece_id: PositiveID
    order_id: PositiveID | None = None


class DeliveryFact(BaseModel):
    order_id: PositiveID
    client_id: PositiveID
    delivery_id: PositiveID


class SagaPaymentResult(PaymentResult):
    saga_id: PositiveID


class SagaDeliveryResult(DeliveryFact):
    saga_id: PositiveID
    accepted: bool = Field(strict=True)
    reason: str = ""
