from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from .database import Base

class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(256), nullable=False)
    email = Column(String(256), unique=True, nullable=False)
    creation_date = Column(DateTime(timezone=True), server_default=func.now())