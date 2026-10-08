# -*- coding: utf-8 -*-
"""Database models definitions. Table representations as class."""
from sqlalchemy import Column, DateTime, Integer, String, ForeignKey, Index, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base


class BaseModel(Base):
    """Base database table representation to reuse."""
    __abstract__ = True
    creation_date = Column(DateTime(timezone=True), server_default=func.now())
    update_date = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        fields = ""
        for column in self.__table__.columns:
            if fields == "":
                fields = f"{column.name}='{getattr(self, column.name)}'"
            else:
                fields = f"{fields}, {column.name}='{getattr(self, column.name)}'"
        return f"<{self.__class__.__name__}({fields})>"

    @staticmethod
    def list_as_dict(items):
        """Returns list of items as dict."""
        return [i.as_dict() for i in items]

    def as_dict(self):
        """Return the item as dict."""
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class Account(BaseModel):
    """Account database table representation. One account per client_id."""
    __tablename__ = "account"
    id = Column(Integer, primary_key=True)
    # client_id references Client.id in the Client microservice's own database.
    # NOT a real ForeignKey: it lives in a different database.
    client_id = Column(Integer, nullable=False, unique=True)
    balance = Column(Integer, nullable=False, default=0)

    transactions = relationship("Transaction", back_populates="account", lazy="joined")

    def as_dict(self):
        """Return the account item as dict."""
        dictionary = super().as_dict()
        dictionary['transactions'] = [t.as_dict() for t in self.transactions]
        return dictionary


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
