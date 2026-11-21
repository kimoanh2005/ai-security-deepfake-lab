import os

# Phải đặt trước khi import app.* (app.main dựng sẵn `app` dùng cấu hình từ môi trường).
os.environ.setdefault("JWT_SECRET", "pytest-secret-0123456789abcdef")

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import BASE_DIR, Settings
from app.core.security import hash_password
from app.main import create_app
from app.models import User

TEST_ROUNDS = 4  # bcrypt rẻ cho test


def alembic_upgrade(engine) -> None:
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        jwt_secret="pytest-secret-0123456789abcdef",
        db_path=str(tmp_path / "lab.db"),
        storage_dir=str(tmp_path / "storage"),
        login_rate_limit_per_minute=1000,
        api_rate_limit_per_minute=100000,
        heavy_rate_limit_per_minute=100000,
    )


@pytest.fixture()
def app(settings: Settings):
    application = create_app(settings)
    alembic_upgrade(application.state.engine)
    yield application
    application.state.engine.dispose()


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def make_user(app):
    def _make(email="user@lab.local", password="user-pass-123", role="user", status="active"):
        with app.state.session_factory() as db:
            db.add(User(email=email, password_hash=hash_password(password, TEST_ROUNDS),
                        full_name=email.split("@")[0], role=role, status=status))
            db.commit()
        return email, password

    return _make


@pytest.fixture()
def login(client):
    def _login(email, password):
        r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return _login
