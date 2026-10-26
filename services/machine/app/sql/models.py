from sqlalchemy import Column, Integer, String
from .database import Base


class WorkPiece(Base):
    __tablename__ = "work_piece"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, nullable=True)
    status = Column(String(32), nullable=False, default="Queued")
