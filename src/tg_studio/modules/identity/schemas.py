from pydantic import BaseModel, EmailStr


class RegisterOwnerRequest(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str | None = None
    phone: str | None = None
    business_name: str
    business_description: str | None = None


class RegisterMasterRequest(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str | None = None
    registration_token: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    """Тело опционально: при пустом refresh_token используется HttpOnly-cookie."""

    refresh_token: str | None = None


class CookieAuthResponse(BaseModel):
    """JWT: HttpOnly-cookies `tg_access` / `tg_refresh` (path=/api), тело JSON и заголовки ответа."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: int
    role: str


class UserResponse(BaseModel):
    id: int
    email: str | None
    telegram_id: int | None
    phone: str | None
    first_name: str
    last_name: str | None
    role: str
    is_email_verified: bool
