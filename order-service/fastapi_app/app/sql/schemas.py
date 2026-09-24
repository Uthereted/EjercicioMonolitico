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

    model_config = ConfigDict(from_attributes=True)

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

    creation_date: datetime
    update_date: datetime


class OrderPost(OrderBase):
    """Schema definition to create a new order."""

class OrderStatusUpdate(BaseModel):
    """Schema used to update an order status."""

    status: str = Field(
        description="New status for the order",
        example="Finished"
    )