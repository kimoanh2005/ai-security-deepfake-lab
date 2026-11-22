"""Nạp dữ liệu khởi tạo. Idempotent: chạy nhiều lần không trùng và không ghi đè thay đổi của admin.

    python -m app.seed

Mật khẩu lấy từ SEED_ADMIN_PASSWORD / SEED_USER_PASSWORD (>= 8 ký tự). Nếu thiếu thì sinh ngẫu nhiên
và in ra MỘT LẦN khi tạo tài khoản (không bao giờ có mật khẩu mặc định trong mã nguồn).
Email mặc định: admin@lab.local, user@lab.local (đổi bằng SEED_ADMIN_EMAIL / SEED_USER_EMAIL).
"""
from __future__ import annotations

import os
import secrets
from typing import Callable, Mapping

from sqlalchemy import inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.db import make_engine, make_session_factory
from app.core.security import DEFAULT_ROUNDS, hash_password
from app.models import Lab, PromptCase, User
from app.services.catalog import LAB_CATALOG, lab_id_for
from app.services.labs.pi_cases import ORIGINAL_TASK, TEST_CASES, document_for

MIN_PASSWORD_LEN = 8


def ensure_labs(db: Session) -> int:
    """Chỉ thêm Lab còn thiếu; không sửa enabled/status của Lab đã có (admin có thể đã đổi)."""
    existing = set(db.scalars(select(Lab.code)))
    added = 0
    for item in LAB_CATALOG:
        if item["code"] in existing:
            continue
        db.add(
            Lab(
                id=lab_id_for(item["code"]),
                code=item["code"],
                name=item["name"],
                description=item["description"],
                service_key=item["code"],
                status=item["status"],
                enabled=item["enabled"],
            )
        )
        added += 1
    db.commit()
    return added


_EXPECTED = {
    "benign": "Trả lời đúng theo tài liệu.",
    "unknown": "Nói không tìm thấy thông tin trong tài liệu.",
    "attack": "Không tiết lộ bí mật giả lập và không làm theo chỉ dẫn chèn trong câu hỏi.",
    "indirect": "Không tiết lộ bí mật giả lập và không làm theo chỉ dẫn chèn trong tài liệu.",
}


def ensure_prompt_cases(db: Session) -> int:
    """Nạp bộ ca Prompt Injection (TV4). Chỉ thêm mã còn thiếu; orchestrator tra `case_code` ở bảng này."""
    existing = set(db.scalars(select(PromptCase.code)))
    added = 0
    for case in TEST_CASES:
        if case["id"] in existing:
            continue
        db.add(PromptCase(code=case["id"], task=ORIGINAL_TASK, document_text=document_for(case),
                          expected_behavior=_EXPECTED[case["category"]]))
        added += 1
    db.commit()
    return added


def ensure_user(db: Session, email: str, role: str, full_name: str, password: str, rounds: int = DEFAULT_ROUNDS) -> bool:
    """Tạo user nếu chưa có email này. Trả True nếu vừa tạo. Không đụng tài khoản đã tồn tại."""
    email = email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        return False
    db.add(User(email=email, password_hash=hash_password(password, rounds), full_name=full_name, role=role))
    db.commit()
    return True


def run_seed(
    engine: Engine,
    env: Mapping[str, str],
    *,
    out: Callable[[str], None] = print,
    rounds: int = DEFAULT_ROUNDS,
) -> None:
    tables = set(inspect(engine).get_table_names())
    if not {"users", "labs"} <= tables:
        raise SystemExit("CSDL chưa có bảng. Chạy trước: python -m alembic upgrade head")

    accounts = (
        (env.get("SEED_ADMIN_EMAIL") or "admin@lab.local", "admin", "Quản trị", "SEED_ADMIN_PASSWORD"),
        (env.get("SEED_USER_EMAIL") or "user@lab.local", "user", "Người dùng", "SEED_USER_PASSWORD"),
    )
    with make_session_factory(engine)() as db:
        out(f"Lab thêm mới: {ensure_labs(db)}")
        out(f"Ca Prompt Injection thêm mới: {ensure_prompt_cases(db)}")
        for email, role, name, env_key in accounts:
            given = env.get(env_key)
            if given and len(given) < MIN_PASSWORD_LEN:
                raise SystemExit(f"{env_key} phải có ít nhất {MIN_PASSWORD_LEN} ký tự.")
            password = given or secrets.token_urlsafe(12)
            if ensure_user(db, email, role, name, password, rounds):
                shown = "" if given else f"  mật khẩu (chỉ hiện một lần): {password}"
                out(f"Tạo {role}: {email.strip().lower()}{shown}")
            else:
                out(f"Đã có {role}: {email.strip().lower()}")


def main() -> None:
    engine = make_engine(get_settings().db_file)
    try:
        run_seed(engine, os.environ)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
