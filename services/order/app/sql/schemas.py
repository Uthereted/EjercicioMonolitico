"""REST contracts for orders and manufacturing callbacks."""
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    detail: Optional[str] = None


class OrderBase(BaseModel):
    number_of_pieces: int
    description: str = "No description"


class OrderPost(OrderBase):
    number_of_pieces: int = Field(gt=0, le=10000)
    client_id: int = Field(gt=0)
    address: str = Field(min_length=1, max_length=500)


class Order(OrderBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    client_id: Optional[int] = None
    address: Optional[str] = None
    unit_price: int
    total_price: int
    payment_completed: bool
    manufacturing_requested: bool
    delivery_id: Optional[int] = None
    integration_error: Optional[str] = None
    creation_date: datetime
    update_date: datetime


class OrderStatusUpdate(BaseModel):
    status: Literal["Finished", "Delivered"]


class Piece(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_id: int
    status: str


class PieceStatusUpdate(BaseModel):
    status: Literal["Manufacturing", "Manufactured"]
