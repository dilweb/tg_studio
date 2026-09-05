import logging

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

from tg_studio.api.auth import (
    REFRESH_COOKIE_NAME,
    CurrentUserDep,
    attach_auth_to_response,
    clear_auth_cookies,
    create_access_token,
    create_email_verification_token,
    create_refresh_token,
    decode_email_verification_token,
    decode_token,
    hash_password,
    smtp_configured,
    verify_password,
)
from tg_studio.api.deps import SessionDep
from tg_studio.db.models import Business, Master, User, UserRole
from tg_studio.modules.identity.schemas import (
    CookieAuthResponse,
    LoginRequest,
    RefreshRequest,
    RegisterMasterRequest,
    RegisterOwnerRequest,
    UserResponse,
)
from tg_studio.tasks.email_verification import send_verification_email_task

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.post("/register/owner", response_model=CookieAuthResponse, status_code=201)
async def register_owner(body: RegisterOwnerRequest, session: SessionDep, response: Response):
    existing = await session.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
        phone=body.phone,
        role=UserRole.owner,
    )
    session.add(user)
    await session.flush()

    business = Business(
        owner_id=user.id,
        owner_telegram_id=None,
        name=body.business_name,
        description=body.business_description,
    )
    session.add(business)
    await session.commit()

    try:
        token = create_email_verification_token(user.id)
        send_verification_email_task.delay(user.email or "", token, user.first_name or "")
    except Exception:
        logger.exception("Failed to enqueue verification email for %s", user.email)

    access = create_access_token(user.id, user.role)
    refresh = create_refresh_token(user.id)
    attach_auth_to_response(response, access, refresh)
    return CookieAuthResponse(
        access_token=access,
        refresh_token=refresh,
        user_id=user.id,
        role=user.role.value,
    )


@router.post("/register/master", response_model=CookieAuthResponse, status_code=201)
async def register_master(body: RegisterMasterRequest, session: SessionDep, response: Response):
    result = await session.execute(
        select(Master).where(
            Master.registration_token == body.registration_token,
            Master.user_id.is_(None),
        )
    )
    master = result.scalar_one_or_none()
    if not master:
        raise HTTPException(status_code=400, detail="Invalid or already used registration token")

    existing = await session.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
        role=UserRole.master,
    )
    session.add(user)
    await session.flush()

    master.user_id = user.id
    master.registration_token = None
    await session.commit()

    try:
        token = create_email_verification_token(user.id)
        send_verification_email_task.delay(user.email or "", token, user.first_name or "")
    except Exception:
        logger.exception("Failed to enqueue verification email for %s", user.email)

    access = create_access_token(user.id, user.role)
    refresh = create_refresh_token(user.id)
    attach_auth_to_response(response, access, refresh)
    return CookieAuthResponse(
        access_token=access,
        refresh_token=refresh,
        user_id=user.id,
        role=user.role.value,
    )


@router.post("/login", response_model=CookieAuthResponse)
async def login(body: LoginRequest, session: SessionDep, response: Response):
    result = await session.execute(
        select(User).where(User.email == body.email, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if smtp_configured() and user.email and not user.is_email_verified:
        raise HTTPException(
            status_code=403,
            detail="Please verify your email. Check your inbox or use POST /api/auth/resend-verification.",
        )

    access = create_access_token(user.id, user.role)
    refresh = create_refresh_token(user.id)
    attach_auth_to_response(response, access, refresh)
    return CookieAuthResponse(
        access_token=access,
        refresh_token=refresh,
        user_id=user.id,
        role=user.role.value,
    )


@router.post("/refresh", response_model=CookieAuthResponse)
async def refresh(request: Request, body: RefreshRequest, session: SessionDep, response: Response):
    refresh_raw = body.refresh_token or request.cookies.get(REFRESH_COOKIE_NAME)
    if not refresh_raw:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    payload = decode_token(refresh_raw)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user_id = int(payload["sub"])
    result = await session.execute(
        select(User).where(User.id == user_id, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    if smtp_configured() and user.email and not user.is_email_verified:
        raise HTTPException(
            status_code=403,
            detail="Please verify your email before refreshing the session.",
        )

    access = create_access_token(user.id, user.role)
    new_refresh = create_refresh_token(user.id)
    attach_auth_to_response(response, access, new_refresh)
    return CookieAuthResponse(
        access_token=access,
        refresh_token=new_refresh,
        user_id=user.id,
        role=user.role.value,
    )


@router.post("/logout")
async def logout(response: Response):
    clear_auth_cookies(response)
    return {"detail": "Logged out"}


@router.get("/verify-email")
async def verify_email(token: str, session: SessionDep):
    user_id = decode_email_verification_token(token)
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_email_verified = True
    await session.commit()
    html = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Verified</title></head>
<body style="font-family:system-ui;text-align:center;padding:48px;">
<h1 style="color:#16a34a;">Email verified</h1>
<p>You can close this window and return to the app.</p>
</body></html>"""
    return HTMLResponse(content=html, status_code=200)


class ResendVerificationRequest(BaseModel):
    email: EmailStr


@router.post("/resend-verification")
async def resend_verification(body: ResendVerificationRequest, session: SessionDep):
    result = await session.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    # Не раскрываем существование аккаунта — всегда 200
    if not user or user.is_email_verified:
        return {"detail": "If the email exists and is unverified, a link has been sent"}
    try:
        token = create_email_verification_token(user.id)
        send_verification_email_task.delay(user.email, token, user.first_name or "")
    except Exception:
        logger.exception("Failed to enqueue verification email for %s", user.email)
        raise HTTPException(status_code=503, detail="Could not send email. Try again later.") from None
    return {"detail": "If the email exists and is unverified, a link has been sent"}


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUserDep):
    return UserResponse(
        id=user.id,
        email=user.email,
        telegram_id=user.telegram_id,
        phone=user.phone,
        first_name=user.first_name,
        last_name=user.last_name,
        role=user.role.value,
        is_email_verified=user.is_email_verified,
    )


