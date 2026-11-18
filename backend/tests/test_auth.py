import logging

import jwt
import pytest

from app.core.ratelimit import SlidingWindowLimiter
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password

URL = "/api/v1/auth/tokens"


def test_login_ok_returns_token_and_no_store(client, make_user):
    email, pw = make_user()
    r = client.post(URL, json={"email": email, "password": pw})
    assert r.status_code == 201
    body = r.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 3600 and body["access_token"]
    assert r.headers["cache-control"] == "no-store"


def test_email_is_case_insensitive(client, make_user):
    email, pw = make_user(email="reviewer1@lab.local")
    r = client.post(URL, json={"email": "  Reviewer1@LAB.local ", "password": pw})
    assert r.status_code == 201


def test_wrong_password_unknown_email_and_disabled_look_identical(client, make_user):
    email, pw = make_user()
    make_user(email="off@lab.local", status="disabled")
    cases = [
        {"email": email, "password": "sai-mat-khau"},
        {"email": "nobody@lab.local", "password": pw},
        {"email": "off@lab.local", "password": pw},
    ]
    responses = [client.post(URL, json=c) for c in cases]
    for r in responses:
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == "Bearer"
        assert r.headers["content-type"].startswith("application/problem+json")
        assert r.json()["code"] == "UNAUTHENTICATED"
    assert len({r.text for r in responses}) == 1  # không lộ tài khoản có tồn tại hay không


def test_login_failure_logged_without_secrets(client, make_user, caplog):
    email, _ = make_user()
    with caplog.at_level(logging.WARNING, logger="app.security"):
        client.post(URL, json={"email": email, "password": "mat-khau-sai-12345"})
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "login_failed" in text
    assert "mat-khau-sai-12345" not in text and email not in text


def test_login_validation_and_malformed_json(client):
    assert client.post(URL, json={"email": "khong-hop-le", "password": "x"}).status_code == 422
    assert client.post(URL, json={"email": "a@b.c"}).status_code == 422
    assert client.post(URL, json={"email": "a@b.c", "password": "x", "extra": 1}).status_code == 422
    r = client.post(URL, content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["code"] == "MALFORMED_REQUEST"


def test_validation_error_does_not_echo_password(client):
    r = client.post(URL, json={"email": "khong-hop-le", "password": "BiMat-Khong-Duoc-Lo"})
    assert r.status_code == 422 and "BiMat-Khong-Duoc-Lo" not in r.text
    assert r.json()["errors"][0]["field"] == "email"


def test_me_requires_token(client, make_user, login):
    assert client.get("/api/v1/users/me").status_code == 401
    assert client.get("/api/v1/users/me", headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401
    assert client.get("/api/v1/users/me", headers={"Authorization": "Basic dXNlcjpwdw=="}).status_code == 401
    email, pw = make_user()
    r = client.get("/api/v1/users/me", headers=login(email, pw))
    assert r.status_code == 200
    me = r.json()
    assert me["email"] == email and me["role"] == "user" and me["id"].startswith("usr_")
    assert "password" not in r.text and "hash" not in r.text


def test_token_of_disabled_or_deleted_user_is_rejected(client, app, make_user, login):
    email, pw = make_user()
    headers = login(email, pw)
    with app.state.session_factory() as db:
        from app.models import User
        db.query(User).filter_by(email=email).update({"status": "disabled"})
        db.commit()
    assert client.get("/api/v1/users/me", headers=headers).status_code == 401


def test_expired_and_forged_tokens_rejected(client, make_user, settings):
    make_user()
    me = "/api/v1/users/me"
    expired = jwt.encode({"sub": "usr_x", "exp": 1}, settings.jwt_secret, algorithm="HS256")
    forged = jwt.encode({"sub": "usr_x", "exp": 4102444800}, "khoa-khac-0123456789abcdef", algorithm="HS256")
    none_alg = jwt.encode({"sub": "usr_x", "exp": 4102444800}, key=None, algorithm="none")
    for t in (expired, forged, none_alg):
        assert client.get(me, headers={"Authorization": f"Bearer {t}"}).status_code == 401


def test_login_rate_limit(tmp_path, make_user):
    from fastapi.testclient import TestClient
    from tests.conftest import alembic_upgrade
    from app.config import Settings
    from app.main import create_app

    s = Settings(_env_file=None, jwt_secret="pytest-secret-0123456789abcdef", db_path=str(tmp_path / "rl.db"),
                 storage_dir=str(tmp_path / "st"), login_rate_limit_per_minute=3)
    application = create_app(s)
    alembic_upgrade(application.state.engine)
    with TestClient(application) as c:
        codes = [c.post(URL, json={"email": "a@b.c", "password": "x"}).status_code for _ in range(4)]
        r = c.post(URL, json={"email": "a@b.c", "password": "x"})
    assert codes == [401, 401, 401, 429]
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and r.json()["code"] == "RATE_LIMITED"


def test_sliding_window_expires():
    t = [0.0]
    lim = SlidingWindowLimiter(2, 60, clock=lambda: t[0])
    assert lim.hit("k")[0] and lim.hit("k")[0]
    ok, _, retry = lim.hit("k")
    assert not ok and retry == 60
    t[0] = 61
    assert lim.hit("k")[0]


def test_password_hashing_rules(settings):
    h = hash_password("mat-khau-tot", rounds=4)
    assert h != "mat-khau-tot" and verify_password("mat-khau-tot", h)
    assert not verify_password("khac", h)
    assert not verify_password("bat-ky", None)  # không có user
    with pytest.raises(ValueError):
        hash_password("x" * 73, rounds=4)
    long_h = hash_password("y" * 72, rounds=4)
    assert not verify_password("y" * 72 + "z", long_h)  # không khớp nhờ cắt ngầm
    token, ttl = create_access_token("usr_1", settings)
    assert ttl == 3600 and decode_access_token(token, settings) == "usr_1"
