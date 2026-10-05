# -*- coding: utf-8 -*-
"""Classes for Request/Response schema definitions."""
# pylint: disable=too-few-public-methods
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict  # pylint: disable=no-name-in-module


class Message(BaseModel):
    """Message schema definition."""
    detail: Optional[str] = Field(example="error or success message")


class OrderBase(BaseModel):
    """Order base schema definition."""
    number_of_pieces: int = Field(
        description="Number of pieces to manufacture for the new order",
        default=None,
        example=10
    )
    description: str = Field(
        description="Human readable description for the order",
        default="No description",
        example="CompanyX order on 2022-01-20"
    )

    #  pieces = relationship("Piece", lazy="joined")


class Order(OrderBase):
    """Order schema definition."""
    model_config = ConfigDict(from_attributes=True)  # ORM mode ON
    id: int = Field(
        description="Primary key/identifier of the order.",
        default=None,
        example=1
    )
    status: str = Field(
        description="Current status of the order",
        default="Created",
        example="Finished"
    )


class OrderPost(OrderBase):
    """Schema definition to create a new order."""


class PieceBase(BaseModel):
    """Piece base schema definition."""
    id: int = Field(
        description="Piece identifier (Primary key)",
        example="1"
    )
    manufacturing_date: Optional[datetime] = Field(
        description="Date when piece has been manufactured",
        example="2022-07-22T17:32:32.193211"
    )
    status: str = Field(
        description="Current status of the piece",
        default="Queued",
        example="Manufactured"
    )


class Piece(PieceBase):
    """Piece schema definition."""
    model_config = ConfigDict(from_attributes=True)  # ORM mode ON
    order: Optional[Order] = Field(description="Order where the piece belongs to")

    #class Config:
    #    """ORM configuration."""
    #    orm_mode = True


class MachineStatusResponse(BaseModel):
    """machine status schema definition."""
    status: str = Field(
        description="Machine's current status",
        default=None,
        example="Waiting"
    )
    working_piece: Optional[int] = Field(
        description="Current working piece id. None if not working piece.",
        example=1
    )
    queue: List[int] = Field(description="Queued piece ids")
