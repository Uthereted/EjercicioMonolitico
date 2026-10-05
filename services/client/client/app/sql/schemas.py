from typing import Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr

class ClientBase(BaseModel):
    name: str
    email: EmailStr

class ClientCreate(ClientBase):
    pass

class ClientResponse(ClientBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    creation_date: Optional[datetime] = None

class Message(BaseModel):
    detail: str