from sqlalchemy import Boolean, Column, DateTime, Integer, String, Index, text
from sqlalchemy.sql import func
from .database import Base

class BaseModel(Base):
    """Base database table representation to reuse."""
    __abstract__ = True
    creation_date = Column(DateTime(timezone=True), server_default=func.now())
    update_date = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Delivery(BaseModel):
    STATUS_PENDING = "Pending"
    STATUS_SENT = "Sent"
    STATUS_DELIVERED = "Delivered"
    STATUS_CANCELED = "Canceled"

    __tablename__ = "delivery"
    __table_args__ = (Index("uq_delivery_order", "order_id", unique=True,
                           sqlite_where=text("workflow_managed = 1")),)

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, nullable=False)
    order_id = Column(Integer, nullable=False)
    address = Column(String(500), nullable=False)
    status = Column(String(50), nullable=False, default="Pending")
    tracking_number = Column(String(100), nullable=True)
    order_notified = Column(Boolean, nullable=False, default=False, server_default="0")
    workflow_managed = Column(Boolean, nullable=False, default=True, server_default="0")
