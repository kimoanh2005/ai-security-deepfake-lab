"""Idempotency-Key của POST /labs/{lab}/experiments (API-01 mục 8)."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models import Experiment, IdempotencyKey
from app.seed import ensure_labs

API = "/api/v1"
URL = f"{API}/labs/api_lab/experiments"
PAYLOAD = {"scenario_code": "burst-30"}


@pytest.fixture()
def seeded(app):
    with app.state.session_factory() as db:
        ensure_labs(db)


@pytest.fixture()
def h(seeded, make_user, login):
    return login(*make_user())


@pytest.fixture()
def h2(seeded, make_user, login):
    return login(*make_user(email="other@lab.local", password="other-pass-123"))


def post(client, headers, key=None, payload=PAYLOAD, url=URL):
    hdr = dict(headers)
    if key is not None:
        hdr["Idempotency-Key"] = key
    return client.post(url, json=payload, headers=hdr)


def count_exp(app) -> int:
    with app.state.session_factory() as db:
        return db.scalar(select(func.count()).select_from(Experiment))


def test_same_key_same_body_returns_same_experiment(client, app, h):
    a = post(client, h, "k-1")
    b = post(client, h, "k-1")
    assert a.status_code == 202 and b.status_code == 202
    assert a.json()["id"] == b.json()["id"]
    assert b.headers["Location"] == a.headers["Location"] == f"{API}/experiments/{a.json()['id']}"
    assert b.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in a.headers
    assert b.headers["Retry-After"]
    app.state.orchestrator.wait_idle(10)
    assert count_exp(app) == 1


def test_replay_returns_current_state(client, app, h):
    first = post(client, h, "k-2").json()
    assert app.state.orchestrator.wait_idle(10)
    again = post(client, h, "k-2")
    assert again.status_code == 202
    body = again.json()
    assert body["id"] == first["id"]
    assert body["state"] in ("done", "failed")  # không phải ảnh chụp `queued` lúc tạo


def test_same_key_different_body_is_422(client, app, h):
    assert post(client, h, "k-3").status_code == 202
    r = post(client, h, "k-3", payload={"scenario_code": "other"})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert r.json()["errors"][0]["field"] == "Idempotency-Key"
    app.state.orchestrator.wait_idle(10)
    assert count_exp(app) == 1


def test_body_key_order_does_not_matter(client, app, h):
    a = post(client, h, "k-4", payload={"a": 1, "scenario_code": "burst-30"})
    b = post(client, h, "k-4", payload={"scenario_code": "burst-30", "a": 1})
    # Cả hai cùng được chấp nhận hoặc cùng bị validate từ chối; không bao giờ là IDEMPOTENCY_KEY_REUSED.
    assert b.json().get("code") != "IDEMPOTENCY_KEY_REUSED"
    assert a.status_code == b.status_code


def test_same_key_other_lab_is_422(client, app, h):
    assert post(client, h, "k-5").status_code == 202
    r = post(client, h, "k-5", url=f"{API}/labs/deepfake_lab/experiments")
    assert r.status_code == 422 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_key_is_scoped_per_user(client, app, h, h2):
    a = post(client, h, "shared")
    b = post(client, h2, "shared")
    assert a.status_code == b.status_code == 202
    assert a.json()["id"] != b.json()["id"]
    assert "Idempotent-Replayed" not in b.headers


def test_different_keys_create_different_experiments(client, app, h):
    a = post(client, h, "x1").json()["id"]
    b = post(client, h, "x2").json()["id"]
    c = post(client, h).json()["id"]
    d = post(client, h).json()["id"]  # không có khóa → luôn tạo mới
    assert len({a, b, c, d}) == 4


def test_expired_key_creates_new_experiment(client, app, h):
    first = post(client, h, "k-6").json()["id"]
    app.state.orchestrator.wait_idle(10)
    with app.state.session_factory() as db:
        rec = db.scalar(select(IdempotencyKey).where(IdempotencyKey.key == "k-6"))
        rec.created_at = "2000-01-01T00:00:00.000Z"
        db.commit()
    r = post(client, h, "k-6", payload=PAYLOAD)
    assert r.status_code == 202 and r.json()["id"] != first
    assert "Idempotent-Replayed" not in r.headers
    # hết hạn thì thân khác cũng được dùng lại khóa
    with app.state.session_factory() as db:
        rec = db.scalar(select(IdempotencyKey).where(IdempotencyKey.key == "k-6"))
        rec.created_at = "2000-01-01T00:00:00.000Z"
        db.commit()
    assert post(client, h, "k-6", payload={"scenario_code": "burst-30", "n": 1}).status_code in (202, 422)


@pytest.mark.parametrize("key", ["", " ", "a b", "x" * 129, "tiếng-việt"])
def test_invalid_key_is_422(client, app, h, key):
    r = client.post(URL, json=PAYLOAD, headers={**h, "Idempotency-Key": key.encode("utf-8")})  # bytes: gửi cả ký tự non-ASCII
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"
    assert r.json()["errors"][0]["field"] == "Idempotency-Key"
    assert count_exp(app) == 0


def test_failed_validation_does_not_consume_key(client, app, h):
    bad = post(client, h, "k-7", payload={"scenario_code": "không-có-thật"})
    assert bad.status_code == 422 and bad.json()["code"] == "VALIDATION_ERROR"
    ok = post(client, h, "k-7")
    assert ok.status_code == 202
    assert count_exp(app) == 1


def test_replay_skips_queue_full_and_lab_disabled(client, app, h):
    first = post(client, h, "k-8").json()["id"]
    app.state.orchestrator.wait_idle(10)
    app.state.settings.max_queued_jobs = 0
    r = post(client, h, "k-8")
    assert r.status_code == 202 and r.json()["id"] == first
    assert post(client, h, "k-8-new").status_code == 503  # khóa mới vẫn bị trần hàng đợi chặn


def test_deleting_experiment_cascades_key(client, app, h):
    exp_id = post(client, h, "k-9").json()["id"]
    app.state.orchestrator.wait_idle(10)
    with app.state.session_factory() as db:
        db.delete(db.get(Experiment, exp_id))
        db.commit()
        assert db.scalar(select(func.count()).select_from(IdempotencyKey)) == 0  # CASCADE
    assert post(client, h, "k-9").json()["id"] != exp_id


def test_requires_auth(client, seeded):
    r = client.post(URL, json=PAYLOAD, headers={"Idempotency-Key": "k"})
    assert r.status_code == 401
