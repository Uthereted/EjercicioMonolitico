"""Facts shared by independently deployed subscribers."""
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


class OrderPaid(OrderCreated):
    address: str = Field(min_length=1, max_length=500)
    piece_ids: list[PositiveID] = Field(min_length=1)
    manufactured_piece_ids: list[PositiveID] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_pieces(self):
        if len(set(self.piece_ids)) != len(self.piece_ids):
            raise ValueError("Duplicate piece identifiers")
        if not set(self.manufactured_piece_ids).issubset(self.piece_ids):
            raise ValueError("Unknown manufactured piece")
        return self


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
