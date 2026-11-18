"""Băm mật khẩu (bcrypt) và JWT (HS256)."""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

import bcrypt
import jwt

from app.config import Settings

ALLOWED_ALGORITHMS = ["HS256"]
BCRYPT_MAX_BYTES = 72  # bcrypt chỉ dùng 72 byte đầu → từ chối mật khẩu dài hơn thay vì cắt ngầm
DEFAULT_ROUNDS = 12


def hash_password(password: str, rounds: int = DEFAULT_ROUNDS) -> str:
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        raise ValueError(f"Mật khẩu dài tối đa {BCRYPT_MAX_BYTES} byte.")
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=rounds)).decode("ascii")


@lru_cache
def _dummy_hash() -> str:
    # Băm giả để thời gian xử lý của "không có user" gần bằng "sai mật khẩu".
    return hash_password("dummy-password-for-timing")


def verify_password(password: str, password_hash: str | None) -> bool:
    raw = password.encode("utf-8")
    target = (password_hash or _dummy_hash()).encode("ascii")
    try:
        ok = bcrypt.checkpw(raw[:BCRYPT_MAX_BYTES], target)
    except ValueError:
        return False
    return ok and password_hash is not None and len(raw) <= BCRYPT_MAX_BYTES


def create_access_token(user_id: str, settings: Settings) -> tuple[str, int]:
    now = dt.datetime.now(dt.timezone.utc)
    ttl = settings.jwt_expire_minutes * 60
    payload = {"sub": user_id, "iat": now, "exp": now + dt.timedelta(seconds=ttl)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256"), ttl


def decode_access_token(token: str, settings: Settings) -> str | None:
    """Trả về user_id hoặc None nếu token sai/hết hạn."""
    try:
        data = jwt.decode(
            token, settings.jwt_secret, algorithms=ALLOWED_ALGORITHMS, options={"require": ["exp", "sub"]}
        )
    except jwt.PyJWTError:
        return None
    sub = data.get("sub")
    return sub if isinstance(sub, str) else None
