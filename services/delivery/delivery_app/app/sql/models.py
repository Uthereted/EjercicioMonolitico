from sqlalchemy import Column, DateTime, Integer, String, TEXT, ForeignKey
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

class Delivery(BaseModel):
    STATUS_PENDING = "Pending"
    STATUS_SENT = "Sent"
    STATUS_DELIVERED = "Delivered"
    STATUS_CANCELED = "Canceled"

    __tablename__ = "delivery"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, nullable=False)
    order_id = Column(Integer, nullable=False)
    address = Column(String(500), nullable=False)
    status = Column(String(50), nullable=False, default="Pending")
    tracking_number = Column(String(100), nullable=True)