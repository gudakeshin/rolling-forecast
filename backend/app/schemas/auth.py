"""Pydantic schemas for authentication."""

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    role: str


class UserCreate(BaseModel):
    email: str
    username: str
    password: str
    full_name: str = ""
    business_unit: str | None = None
    role_name: str = "analyst"


class UserResponse(BaseModel):
    id: str
    email: str
    username: str
    full_name: str
    business_unit: str | None
    role_name: str
    is_active: bool

    model_config = {"from_attributes": True}
