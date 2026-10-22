from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field

class RoleSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str
    roles: List[str] = Field(default_factory=lambda: ["user"])

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    email: str
    roles: List[RoleSchema]
    creation_date: Optional[datetime] = None

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class ClientBase(BaseModel):
    name: str
    email: EmailStr

class ClientCreate(ClientBase):
    pass

class ClientResponse(ClientBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    creation_date: Optional[datetime] = None
