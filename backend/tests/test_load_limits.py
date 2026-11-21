"""Giới hạn tải: rate limit API điều khiển (theo người dùng) và trần hàng đợi job."""
from __future__ import annotations

import threading

import pytest

from app.core.errors import AppError
from app.core.ratelimit import SlidingWindowLimiter
from tests.test_experiments_api import API, PAYLOAD, h, h2, seeded  # noqa: F401  (fixtures dùng chung)
from tests.test_orchestrator import API_PAYLOAD, FakeService, make_orch, ok_result, user_id  # noqa: F401


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def set_limit(app, name, limit, clock=None):
    app.state.limiters[name] = SlidingWindowLimiter(limit, 60, **({"clock": clock} if clock else {}))


def create(client, headers):
    return client.post(f"{API}/labs/api_lab/experiments", json=PAYLOAD, headers=headers)


# ---- rate limit "heavy": tạo experiment, tải tệp ----

def test_heavy_limit_on_create_experiment(client, app, h):
    set_limit(app, "heavy", 2)
    assert create(client, h).status_code == 202
    assert create(client, h).status_code == 202
    r = create(client, h)
    assert r.status_code == 429 and r.json()["code"] == "RATE_LIMITED"
    assert r.headers["content-type"].startswith("application/problem+json")
    assert int(r.headers["Retry-After"]) >= 1
    assert r.headers["RateLimit-Limit"] == "2" and r.headers["RateLimit-Remaining"] == "0"


def test_heavy_limit_is_per_user(client, app, h, h2):
    set_limit(app, "heavy", 1)
    assert create(client, h).status_code == 202
    assert create(client, h).status_code == 429
    assert create(client, h2).status_code == 202  # người khác không bị ảnh hưởng


def test_heavy_limit_recovers_after_window(client, app, h):
    clock = Clock()
    set_limit(app, "heavy", 1, clock)
    assert create(client, h).status_code == 202
    assert create(client, h).status_code == 429
    clock.t += 61
    assert create(client, h).status_code == 202


def test_heavy_limit_on_media_upload(client, app, h):
    set_limit(app, "heavy", 2)
    fields = {"label": "original", "source": "s", "consent_ref": "c"}
    codes = [client.post(f"{API}/media-assets", headers=h, data=fields, files={"file": ("a.mp4", b"x" * 20, "video/mp4")}).status_code
             for _ in range(3)]
    assert codes == [415, 415, 429]


def test_rejected_by_rate_limit_creates_no_experiment(client, app, h):
    set_limit(app, "heavy", 1)
    create(client, h)
    create(client, h)
    app.state.orchestrator.wait_idle(10)
    assert client.get(f"{API}/experiments", headers=h).json()["total"] == 1


# ---- rate limit "control": mọi request đã xác thực ----

def test_control_limit_applies_to_reads(client, app, h):
    set_limit(app, "control", 3)
    assert [client.get(f"{API}/labs", headers=h).status_code for _ in range(4)] == [200, 200, 200, 429]
    assert client.get(f"{API}/experiments", headers=h).status_code == 429  # dùng chung một hạn mức cho nhóm endpoint
    assert client.get(f"{API}/media-assets", headers=h).status_code == 429


def test_success_responses_carry_ratelimit_headers(client, app, h):
    set_limit(app, "control", 5)
    r = client.get(f"{API}/labs", headers=h)
    assert r.headers["RateLimit-Limit"] == "5" and r.headers["RateLimit-Remaining"] == "4"


def test_unauthenticated_gets_401_and_is_not_counted(client, app, h):
    set_limit(app, "control", 2)
    for _ in range(5):
        assert client.get(f"{API}/labs").status_code == 401
    assert client.get(f"{API}/labs", headers=h).status_code == 200


def test_health_login_and_sandbox_unaffected(client, app, h):
    set_limit(app, "control", 1)
    set_limit(app, "heavy", 1)
    client.get(f"{API}/labs", headers=h)
    assert client.get(f"{API}/labs", headers=h).status_code == 429
    assert client.get(f"{API}/health").status_code in (200, 503)
    assert client.post(f"{API}/auth/login", json={"email": "user@lab.local", "password": "user-pass-123"}).status_code == 200


# ---- trần hàng đợi ----

def test_queue_full_rejects_and_recovers(app, make_orch, user_id):
    gate, started = threading.Event(), threading.Event()

    def slow(ctx, payload):
        started.set()
        gate.wait(10)
        return ok_result()

    orch = make_orch(slow, max_queued_jobs=2)
    orch.start()
    try:
        orch.submit(user_id, "api_lab", API_PAYLOAD)
        assert started.wait(5)           # job 1 đang chạy, hàng đợi trống
        orch.submit(user_id, "api_lab", API_PAYLOAD)
        orch.submit(user_id, "api_lab", API_PAYLOAD)   # hàng đợi: 2/2
        with pytest.raises(AppError) as exc:
            orch.submit(user_id, "api_lab", API_PAYLOAD)
        assert exc.value.status == 503 and exc.value.code == "QUEUE_FULL"
        assert int(exc.value.headers["Retry-After"]) >= 1
        from sqlalchemy import func, select
        from app.models import Experiment
        with app.state.session_factory() as db:
            assert db.scalar(select(func.count()).select_from(Experiment)) == 3  # job bị từ chối không để lại dòng nào
        gate.set()
        assert orch.wait_idle(10)
        orch.submit(user_id, "api_lab", API_PAYLOAD)   # có chỗ trở lại
    finally:
        gate.set()


def test_queue_full_http_shape(client, app, h, make_orch):
    gate, started = threading.Event(), threading.Event()

    def slow(ctx, payload):
        started.set()
        gate.wait(10)
        return ok_result()

    orch = make_orch(slow, max_queued_jobs=1)
    orch.start()
    app.state.orchestrator = orch
    try:
        assert create(client, h).status_code == 202
        assert started.wait(5)
        assert create(client, h).status_code == 202
        r = create(client, h)
        assert r.status_code == 503 and r.json()["code"] == "QUEUE_FULL"
        assert r.headers["content-type"].startswith("application/problem+json") and "Retry-After" in r.headers
    finally:
        gate.set()


def test_validation_error_wins_over_queue_full(app, make_orch, user_id):
    orch = make_orch(lambda ctx, p: ok_result(), max_queued_jobs=1)  # không start: hàng đợi không bị rút
    orch.submit(user_id, "api_lab", API_PAYLOAD)
    with pytest.raises(AppError) as exc:
        orch.submit(user_id, "api_lab", {"bad": 1})
    assert exc.value.status == 422
