"""/sandbox/echo (T-12, FR-API-01): bật/tắt rate limit theo request, bộ đếm riêng, không ảnh hưởng /api/v1."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.sandbox import MAX_RUNS_TRACKED, SandboxState
from app.main import create_app
from tests.conftest import alembic_upgrade


def hdr(app, mode=None, run=None, token=True):
    h = {}
    if token:
        h["X-Lab-Token"] = app.state.settings.sandbox_token
    if mode is not None:
        h["X-Lab-Rate-Limit"] = mode
    if run is not None:
        h["X-Lab-Run"] = run
    return h


@pytest.fixture()
def small_app(tmp_path):
    s = Settings(_env_file=None, jwt_secret="pytest-secret-0123456789abcdef", db_path=str(tmp_path / "l.db"),
                 storage_dir=str(tmp_path / "st"), login_rate_limit_per_minute=1000,
                 sandbox_max_requests=3, sandbox_window_seconds=60)
    a = create_app(s)
    alembic_upgrade(a.state.engine)
    yield a
    a.state.engine.dispose()


@pytest.fixture()
def sc(small_app):
    with TestClient(small_app) as c:
        yield c


# ---- SandboxState (đơn vị) ----

def test_state_on_blocks_after_limit_and_window_slides():
    now = [0.0]
    st = SandboxState(2, 10, clock=lambda: now[0])
    assert st.hit("r", "on")["allowed"] and st.hit("r", "on")["allowed"]
    blocked = st.hit("r", "on")
    assert blocked["allowed"] is False and blocked["retry_after"] == 10 and blocked["remaining"] == 0
    now[0] = 10.0
    assert st.hit("r", "on")["allowed"]


def test_state_off_never_blocks_and_counts_separately():
    st = SandboxState(1, 60)
    for i in range(1, 6):
        r = st.hit("r", "off")
        assert r["allowed"] and r["count"] == i and r["remaining"] is None
    assert st.hit("r", "on")["allowed"]  # bộ đếm "on" không bị ảnh hưởng bởi "off"
    assert st.hit("r", "on")["allowed"] is False


def test_state_runs_are_isolated_and_bounded():
    st = SandboxState(1, 60)
    assert st.hit("a", "on")["allowed"] and st.hit("a", "on")["allowed"] is False
    assert st.hit("b", "on")["allowed"]
    for i in range(MAX_RUNS_TRACKED + 10):
        st.hit(f"x{i}", "off")
    assert len(st._runs) == MAX_RUNS_TRACKED


# ---- endpoint ----

def test_requires_token(sc, small_app):
    assert sc.get("/sandbox/echo").status_code == 401
    r = sc.get("/sandbox/echo", headers={"X-Lab-Token": "sai"})
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "UNAUTHENTICATED"
    assert sc.post("/sandbox/echo", json={}).status_code == 401
    # JWT đăng nhập KHÔNG thay được token Lab
    assert sc.get("/sandbox/echo", headers={"Authorization": "Bearer x"}).status_code == 401


def test_token_is_generated_and_not_default(small_app):
    tok = small_app.state.settings.sandbox_token
    assert len(tok) >= 32


def test_configured_token_is_kept(tmp_path):
    s = Settings(_env_file=None, jwt_secret="pytest-secret-0123456789abcdef", db_path=str(tmp_path / "l.db"),
                 storage_dir=str(tmp_path / "st"), sandbox_token="my-fixed-token-0123456789")
    a = create_app(s)
    assert a.state.settings.sandbox_token == "my-fixed-token-0123456789"
    a.state.engine.dispose()


def test_default_mode_is_on_and_shape(sc, small_app):
    r = sc.get("/sandbox/echo", headers=hdr(small_app))
    assert r.status_code == 200
    assert r.json() == {"ok": True, "mode": "on", "count": 1, "method": "GET", "received_bytes": 0}
    assert r.headers["ratelimit-limit"] == "3" and r.headers["ratelimit-remaining"] == "2"


def test_on_returns_429_with_retry_after_off_does_not(sc, small_app):
    on = hdr(small_app, "on", "run1")
    off = hdr(small_app, "off", "run1")
    assert [sc.get("/sandbox/echo", headers=on).status_code for _ in range(5)] == [200, 200, 200, 429, 429]
    r = sc.get("/sandbox/echo", headers=on)
    assert r.json()["code"] == "RATE_LIMITED" and int(r.headers["retry-after"]) >= 1
    assert r.headers["content-type"].startswith("application/problem+json")
    # cùng khóa chạy, tắt rate limit: mọi request đều 200
    assert all(sc.get("/sandbox/echo", headers=off).status_code == 200 for _ in range(10))


def test_two_modes_give_different_results_for_same_burst(sc, small_app):
    def burst(mode, run):
        return [sc.get("/sandbox/echo", headers=hdr(small_app, mode, run)).status_code for _ in range(8)]
    a, b = burst("off", "m-off"), burst("on", "m-on")
    assert a.count(200) == 8 and b.count(200) == 3 and b.count(429) == 5


def test_runs_do_not_share_counters(sc, small_app):
    for _ in range(4):
        sc.get("/sandbox/echo", headers=hdr(small_app, "on", "exp_a"))
    assert sc.get("/sandbox/echo", headers=hdr(small_app, "on", "exp_a")).status_code == 429
    assert sc.get("/sandbox/echo", headers=hdr(small_app, "on", "exp_b")).status_code == 200


@pytest.mark.parametrize("mode", ["yes", "", "ON1", "true"])
def test_bad_mode_is_422(sc, small_app, mode):
    r = sc.get("/sandbox/echo", headers=hdr(small_app, mode))
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"
    assert r.json()["errors"][0]["field"] == "X-Lab-Rate-Limit"


@pytest.mark.parametrize("run", ["a b", "../x", "x" * 65, "http://evil"])
def test_bad_run_key_is_422(sc, small_app, run):
    r = sc.get("/sandbox/echo", headers=hdr(small_app, "off", run))
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "X-Lab-Run"


def test_mode_header_is_case_insensitive(sc, small_app):
    assert sc.get("/sandbox/echo", headers=hdr(small_app, " OFF ")).json()["mode"] == "off"


def test_post_echoes_only_size_and_keys(sc, small_app):
    body = {"url": "http://169.254.169.254/latest/meta-data", "target": "https://evil.example", "b": 1}
    r = sc.post("/sandbox/echo", json=body, headers=hdr(small_app, "off", "p1"))
    assert r.status_code == 200
    j = r.json()
    assert j["method"] == "POST" and j["keys"] == ["b", "target", "url"] and j["received_bytes"] > 0
    assert "169.254" not in r.text and "evil" not in r.text  # không echo giá trị


def test_post_with_url_does_not_cause_outbound_call(sc, small_app, monkeypatch):
    import httpx, socket
    def boom(*a, **k):
        raise AssertionError("sandbox không được gọi ra ngoài")
    # TestClient chạy trong tiến trình (không dùng mạng); mọi kết nối socket hay HTTPTransport thật đều là lỗi
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    r = sc.post("/sandbox/echo", json={"url": "http://127.0.0.1:1/x"}, headers=hdr(small_app, "off"))
    assert r.status_code == 200


def test_post_non_json_body_ok_and_large_body_413(sc, small_app):
    r = sc.post("/sandbox/echo", content=b"\xff\xfe not json", headers=hdr(small_app, "off"))
    assert r.status_code == 200 and "keys" not in r.json() and r.json()["received_bytes"] == 11
    r = sc.post("/sandbox/echo", content=b"x" * 5000, headers=hdr(small_app, "off"))
    assert r.status_code == 413 and r.json()["code"] == "PAYLOAD_TOO_LARGE"


def test_unauthenticated_large_body_is_rejected_before_reading(sc):
    assert sc.post("/sandbox/echo", content=b"x" * 5000).status_code == 401


def test_sandbox_counters_do_not_affect_api_v1(app, client, make_user, login):
    make_user()
    h = hdr(app, "on", "iso")
    codes = [client.get("/sandbox/echo", headers=h).status_code for _ in range(app.state.sandbox.max_requests + 5)]
    assert codes.count(429) == 5  # sandbox đã bị chặn
    assert client.get("/api/v1/health").status_code == 200
    assert login("user@lab.local", "user-pass-123")["Authorization"].startswith("Bearer ")  # API điều khiển không bị ảnh hưởng


def test_sandbox_not_under_api_v1(sc, small_app):
    assert sc.get("/api/v1/sandbox/echo", headers=hdr(small_app)).status_code == 404


def test_methods_other_than_get_post(sc, small_app):
    r = sc.put("/sandbox/echo", headers=hdr(small_app))
    assert r.status_code == 405


# ---- cấu hình ----

@pytest.mark.parametrize("url", ["http://evil.com", "http://127.0.0.1.evil.com", "http://user@127.0.0.1:8000",
                                 "ftp://127.0.0.1", "http://127.0.0.1:8000/api", "http://169.254.169.254"])
def test_base_url_must_be_loopback(url):
    with pytest.raises(ValueError):
        Settings(_env_file=None, jwt_secret="pytest-secret-0123456789abcdef", sandbox_base_url=url)


@pytest.mark.parametrize("url,norm", [("http://127.0.0.1:8001", "http://127.0.0.1:8001"),
                                      ("http://localhost:8000/", "http://localhost:8000")])
def test_base_url_loopback_ok(url, norm):
    s = Settings(_env_file=None, jwt_secret="pytest-secret-0123456789abcdef", sandbox_base_url=url)
    assert s.sandbox_base_url == norm


def test_defaults(settings):
    assert (settings.sandbox_window_seconds, settings.sandbox_max_requests, settings.max_api_lab_requests) == (10, 20, 100)
