"""Dependency của FastAPI: phiên DB, người dùng hiện tại, kiểm tra vai trò (Cổng 1)."""
from __future__ import annotations

from typing import Iterator

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.models import User

_bearer = HTTPBearer(auto_error=False)
WWW_AUTH = {"WWW-Authenticate": "Bearer"}


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def _unauthenticated() -> AppError:
    return AppError(401, "UNAUTHENTICATED", "Thiếu, sai hoặc hết hạn token.", headers=WWW_AUTH)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise _unauthenticated()
    user_id = decode_access_token(creds.credentials, settings)
    user = db.get(User, user_id) if user_id else None
    if user is None or user.status != "active":
        raise _unauthenticated()
    return user


def require_role(*roles: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise AppError(403, "FORBIDDEN", "Bạn không có quyền thực hiện thao tác này.")
        return user

    return dependency


require_admin = require_role("admin")
