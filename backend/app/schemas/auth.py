"""Pydantic schemas for authentication."""

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    user_id: str
    username: str
    role: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


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
    can_input: bool = False
    can_generate: bool = False
    can_override: bool = False
    can_review: bool = False
    can_publish: bool = False
    can_admin: bool = False
    can_manage_drivers: bool = False

    model_config = {"from_attributes": True}
