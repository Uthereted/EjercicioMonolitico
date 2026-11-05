from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserTokenResponse(TokenResponse):
    refresh_token: str
    expires_in: int = 3600
    refresh_expires_in: int

class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=4096)

class ClientBase(BaseModel):
    name: str
    email: EmailStr

class ClientResponse(ClientBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: Optional[str] = None
    address: Optional[str] = None
    postal_code: Optional[str] = None
    creation_date: Optional[datetime] = None


class ClientRegistration(ClientBase):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1)
    address: str = Field(min_length=1, max_length=500, pattern=r"\S")
    postal_code: str = Field(pattern=r"^[0-9]{5}$")
    roles: List[str] = Field(default_factory=lambda: ["user"])
