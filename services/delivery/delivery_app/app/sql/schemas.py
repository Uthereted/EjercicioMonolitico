# -*- coding: utf-8 -*-
"""Classes for Request/Response schema definitions."""
# pylint: disable=too-few-public-methods
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict  # pylint: disable=no-name-in-module

class Message(BaseModel):
    """Generic API message."""

    detail: Optional[str] = None


class DeliveryBase(BaseModel):
    """Shared delivery fields."""

    client_id: int = Field(..., example=1)
    order_id: int = Field(..., example=100)
    address: str = Field(..., max_length=500, example="Main Street 10")
    status: str = Field(default="Pending", example="Pending")
    tracking_number: Optional[str] = Field(
        default=None,
        max_length=100,
        example="TRACK-12345",
    )


class DeliveryPost(DeliveryBase):
    """Fields required to create a delivery."""


class Delivery(DeliveryBase):
    """Delivery returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    creation_date: Optional[datetime] = None
    update_date: Optional[datetime] = None