# -*- coding: utf-8 -*-
"""Minimal SQLAlchemy models needed by the Machine simulator."""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class Order(Base):
    """Manufacturing order, made up of one or more pieces."""
    __tablename__ = "orders"

    STATUS_PENDING = "Pending"
    STATUS_FINISHED = "Finished"

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String, default=STATUS_PENDING)

    pieces = relationship("Piece", back_populates="order")

    def as_dict(self):
        """Return a plain dict representation."""
        return {"id": self.id, "status": self.status}


class Piece(Base):
    """A single piece manufactured by the machine."""
    __tablename__ = "pieces"

    STATUS_QUEUED = "Queued"
    STATUS_MANUFACTURING = "Manufacturing"
    STATUS_MANUFACTURED = "Manufactured"

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String, default=STATUS_QUEUED)
    manufacturing_date = Column(DateTime, nullable=True)
    order_id = Column(Integer, ForeignKey("orders.id"))

    order = relationship("Order", back_populates="pieces")

    def as_dict(self):
        """Return a plain dict representation."""
        return {
            "id": self.id,
            "status": self.status,
            "manufacturing_date": self.manufacturing_date,
            "order_id": self.order_id,
        }
