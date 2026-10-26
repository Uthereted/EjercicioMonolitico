# -*- coding: utf-8 -*-
"""Database models definitions. Table representations as class."""
from sqlalchemy import Boolean, Column, DateTime, Integer, String, ForeignKey, Index, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base


class BaseModel(Base):
    """Base database table representation to reuse."""
    __abstract__ = True
    creation_date = Column(DateTime(timezone=True), server_default=func.now())
    update_date = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Account(BaseModel):
    """Account database table representation. One account per client_id."""
    __tablename__ = "account"
    id = Column(Integer, primary_key=True)
    # client_id references Client.id in the Client microservice's own database.
    # NOT a real ForeignKey: it lives in a different database.
    client_id = Column(Integer, nullable=False, unique=True)
    balance = Column(Integer, nullable=False, default=0)

    transactions = relationship("Transaction", back_populates="account", lazy="joined")



class Transaction(BaseModel):
    """Transaction database table representation."""
    TYPE_DEPOSIT = "Deposit"
    TYPE_CHARGE = "Charge"

    __tablename__ = "transaction"
    __table_args__ = (
        Index("uq_charge_order", "order_id", unique=True,
              sqlite_where=text("type = 'Charge'")),
    )
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('account.id', ondelete='cascade'), nullable=False)
    amount = Column(Integer, nullable=False)
    type = Column(String(50), nullable=False)
    # order_id references Order.id in the Order microservice (nullable: deposits have none).
    order_id = Column(Integer, nullable=True)

    account = relationship('Account', back_populates='transactions')


class OrderPaymentResult(BaseModel):
    """Retain accepted and rejected outcomes across message redelivery."""
    __tablename__ = "order_payment_result"
    order_id = Column(Integer, primary_key=True)
    client_id = Column(Integer, nullable=False)
    amount = Column(Integer, nullable=False)
    accepted = Column(Boolean, nullable=False)
    reason = Column(String(500), nullable=False)


class KnownClient(Base):
    __tablename__ = "known_client"
    id = Column(Integer, primary_key=True)
