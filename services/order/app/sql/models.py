# -*- coding: utf-8 -*-
"""Database models definitions. Table representations as class."""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, TEXT
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
                # fields = "{}='{}'".format(column.name, getattr(self, column.name))
            else:
                fields = f"{fields}, {column.name}='{getattr(self, column.name)}'"
                # fields = "{}, {}='{}'".format(fields, column.name, getattr(self, column.name))
        return f"<{self.__class__.__name__}({fields})>"
        # return "<{}({})>".format(self.__class__.__name__, fields)

    @staticmethod
    def list_as_dict(items):
        """Returns list of items as dict."""
        return [i.as_dict() for i in items]

    def as_dict(self):
        """Return the item as dict."""
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class Order(BaseModel):
    """order database table representation."""
    STATUS_CREATED = "Created"
    STATUS_FINISHED = "Finished"

    __tablename__ = "manufacturing_order"
    __table_args__ = {"sqlite_autoincrement": True}
    id = Column(Integer, primary_key=True)
    number_of_pieces = Column(Integer, nullable=False)
    description = Column(TEXT, nullable=False, default="No description")
    status = Column(String(256), nullable=False, default=STATUS_CREATED)
    client_id = Column(Integer, nullable=True)
    address = Column(String(500), nullable=True)
    unit_price = Column(Integer, nullable=False, default=10, server_default="10")
    total_price = Column(Integer, nullable=False, default=0, server_default="0")
    payment_completed = Column(Boolean, nullable=False, default=False, server_default="0")
    manufacturing_requested = Column(Boolean, nullable=False, default=False, server_default="0")
    delivery_id = Column(Integer, nullable=True)
    integration_error = Column(TEXT, nullable=True)


class Piece(BaseModel):
    """Durable manufacturing state owned by the order service."""
    __tablename__ = "piece"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("manufacturing_order.id"), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="Queued")
