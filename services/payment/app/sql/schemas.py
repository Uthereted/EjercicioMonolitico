# -*- coding: utf-8 -*-
"""Classes for Request/Response schema definitions."""
# pylint: disable=too-few-public-methods
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict  # pylint: disable=no-name-in-module


class Message(BaseModel):
    """Message schema definition."""
    detail: Optional[str] = Field(example="error or success message")


class TransactionBase(BaseModel):
    """Transaction base schema definition."""
    amount: int = Field(
        description="Amount of the transaction. Always a positive number.",
        example=100,
        gt=0
    )


class DepositPost(TransactionBase):
    """Schema definition to perform a deposit."""


class ChargePost(TransactionBase):
    """Schema definition to perform a charge."""
    order_id: int = Field(
        description="Identifier of the order that originates the charge.",
        example=1
    )


class Transaction(TransactionBase):
    """Transaction schema definition."""
    model_config = ConfigDict(from_attributes=True)  # ORM mode ON
    id: int = Field(description="Primary key/identifier of the transaction.", example=1)
    type: str = Field(description="Transaction type.", example="Deposit")
    order_id: Optional[int] = Field(
        description="Order that originated the charge, if any.",
        default=None,
        example=1
    )


class AccountBase(BaseModel):
    """Account base schema definition."""
    client_id: int = Field(
        description="Identifier of the client this account belongs to.",
        example=1
    )


class Account(AccountBase):
    """Account schema definition."""
    model_config = ConfigDict(from_attributes=True)  # ORM mode ON
    id: int = Field(description="Primary key/identifier of the account.", example=1)
    balance: int = Field(description="Current account balance.", example=500)
    transactions: List[Transaction] = Field(description="Account's transaction history.")


class BalanceResponse(BaseModel):
    """Balance response schema definition."""
    client_id: int = Field(description="Identifier of the client.", example=1)
    balance: int = Field(description="Current account balance.", example=500)