"""POST /auth/login (đăng nhập bằng email) và GET /users/me."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import AppError
from app.core.ratelimit import client_ip, mask_ip, rate_limit
from app.core.security import create_access_token, verify_password
from app.deps import WWW_AUTH, get_current_user, get_db, get_settings_dep
from app.models import User
from app.models.schemas.auth import LoginRequest, TokenResponse, UserOut

router = APIRouter(tags=["auth"])
security_log = logging.getLogger("app.security")


@router.post(
    "/auth/login",
    status_code=200,
    response_model=TokenResponse,
    operation_id="login",
    dependencies=[Depends(rate_limit("login"))],
)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    user = db.scalar(select(User).where(User.email == body.email))
    # Luôn băm/so khớp (kể cả khi không có user) để thời gian phản hồi không lộ tài khoản có tồn tại hay không.
    password_ok = verify_password(body.password, user.password_hash if user else None)
    if user is None or not password_ok or user.status != "active":
        # Chỉ ghi IP đã che một phần; không ghi mật khẩu/email (SPEC 10.3).
        security_log.warning("login_failed ip=%s", mask_ip(client_ip(request)))
        raise AppError(401, "UNAUTHENTICATED", "Email hoặc mật khẩu không đúng.", headers=WWW_AUTH)
    token, ttl = create_access_token(user.id, settings)
    response.headers["Cache-Control"] = "no-store"
    return TokenResponse(access_token=token, expires_in=ttl)


@router.get("/users/me", response_model=UserOut, operation_id="get_current_user")
def read_me(user: User = Depends(get_current_user)):
    return UserOut(id=user.id, email=user.email, full_name=user.full_name, role=user.role, created_at=user.created_at)
