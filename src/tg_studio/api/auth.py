"""
Authentication module.

Supports two auth strategies:
- JWT Bearer tokens (web panel for owners/masters)
- Telegram initData HMAC (Mini App fallback for owners/masters)

Clients authenticate via Telegram bot natively (telegram_id from message.from_user.id).
"""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import parse_qsl, unquote

import jwt
from fastapi import Depends, Header, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.config import settings
from tg_studio.db.models import User, UserRole
from tg_studio.db.session import get_session

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

bearer_scheme = HTTPBearer(auto_error=False)

INIT_DATA_MAX_AGE_SECONDS = 86400


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def create_access_token(user_id: int, role: UserRole) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "role": role.value,
        "type": "access",
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: int) -> str:
    expire = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_token_expire_days)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


ACCESS_COOKIE_NAME = "tg_access"
REFRESH_COOKIE_NAME = "tg_refresh"
AUTH_COOKIE_PATH = "/api"
REFRESH_TOKEN_HEADER = "X-Refresh-Token"
# Заголовки ответа с токенами (для fetch + expose_headers в CORS)
AUTH_RESPONSE_EXPOSE_HEADERS = ("Authorization", REFRESH_TOKEN_HEADER)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from None


def create_email_verification_token(user_id: int) -> str:
    expire = datetime.now(UTC) + timedelta(hours=settings.jwt_email_verify_expire_hours)
    payload = {
        "sub": str(user_id),
        "type": "email_verify",
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_email_verification_token(token: str) -> int:
    try:
        data = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="Verification link has expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid verification link") from None
    if data.get("type") != "email_verify":
        raise HTTPException(status_code=400, detail="Invalid verification link")
    try:
        return int(data["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid verification link") from None


def attach_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    access_max_age = settings.jwt_access_token_expire_minutes * 60
    refresh_max_age = settings.jwt_refresh_token_expire_days * 86400
    base: dict = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "path": AUTH_COOKIE_PATH,
    }
    if settings.cookie_domain:
        base["domain"] = settings.cookie_domain
    response.set_cookie(ACCESS_COOKIE_NAME, access_token, max_age=access_max_age, **base)
    response.set_cookie(REFRESH_COOKIE_NAME, refresh_token, max_age=refresh_max_age, **base)


def attach_auth_token_headers(response: Response, access_token: str, refresh_token: str) -> None:
    response.headers["Authorization"] = f"Bearer {access_token}"
    response.headers[REFRESH_TOKEN_HEADER] = refresh_token


def attach_auth_to_response(response: Response, access_token: str, refresh_token: str) -> None:
    """HttpOnly-cookies + заголовки ответа (Bearer и refresh) + тело задаётся в эндпоинте."""
    attach_auth_cookies(response, access_token, refresh_token)
    attach_auth_token_headers(response, access_token, refresh_token)


def clear_auth_cookies(response: Response) -> None:
    base: dict = {"path": AUTH_COOKIE_PATH}
    if settings.cookie_domain:
        base["domain"] = settings.cookie_domain
    response.delete_cookie(ACCESS_COOKIE_NAME, **base)
    response.delete_cookie(REFRESH_COOKIE_NAME, **base)


# ---------------------------------------------------------------------------
# Telegram initData validation (kept for Mini App fallback)
# ---------------------------------------------------------------------------

def _validate_init_data(init_data: str, bot_token: str) -> dict:
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Missing hash in initData")

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed.items())
    )

    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode(),
        hashlib.sha256,
    ).digest()

    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        raise HTTPException(status_code=401, detail="Invalid initData signature")

    auth_date = int(parsed.get("auth_date", 0))
    if time.time() - auth_date > INIT_DATA_MAX_AGE_SECONDS:
        raise HTTPException(status_code=401, detail="initData expired")

    user_raw = parsed.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="No user in initData")

    return json.loads(unquote(user_raw))


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

_UNVERIFIED_ALLOWED_PATHS = frozenset({
    "/api/auth/me",
})


def smtp_configured() -> bool:
    return bool(settings.smtp_user and settings.smtp_password and settings.api_public_url)


def _require_email_verified_if_applicable(request: Request, user: User) -> None:
    if not smtp_configured() or not user.email or user.is_email_verified:
        return
    path = request.url.path
    if path in _UNVERIFIED_ALLOWED_PATHS:
        return
    raise HTTPException(
        status_code=403,
        detail="Please verify your email. Open the link we sent you, or POST /api/auth/resend-verification.",
    )


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
) -> User:
    """
    Unified auth dependency. Accepts either:
    - Bearer <JWT>           — web panel login
    - TelegramInitData <...> — Mini App fallback
    """
    # Debug mode stub
    if settings.debug and credentials is None and authorization is None:
        result = await session.execute(select(User).limit(1))
        user = result.scalar_one_or_none()
        if user:
            return user
        raise HTTPException(status_code=401, detail="No users in DB (debug mode)")

    # Path 1: JWT Bearer or HttpOnly access cookie
    bearer_or_cookie: str | None = None
    if credentials:
        bearer_or_cookie = credentials.credentials
    elif raw := request.cookies.get(ACCESS_COOKIE_NAME):
        bearer_or_cookie = raw

    if bearer_or_cookie:
        payload = decode_token(bearer_or_cookie)
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user_id = int(payload["sub"])
        result = await session.execute(
            select(User).where(User.id == user_id, User.is_active.is_(True))
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401, detail="User not found or inactive")
        _require_email_verified_if_applicable(request, user)
        return user

    # Path 2: Telegram initData (Mini App fallback)
    if authorization and authorization.startswith("TelegramInitData "):
        init_data = authorization.removeprefix("TelegramInitData ")
        tg_user = _validate_init_data(init_data, settings.bot_token)
        result = await session.execute(
            select(User).where(
                User.telegram_id == tg_user["id"],
                User.is_active.is_(True),
            )
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=401,
                detail="Telegram user not linked to any account",
            )
        _require_email_verified_if_applicable(request, user)
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authorization required: Bearer <token> or TelegramInitData <data>",
    )


CurrentUserDep = Annotated[User, Depends(get_current_user)]
