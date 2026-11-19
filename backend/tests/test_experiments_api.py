"""T-09 / T-17: hợp đồng REST của experiments (mã trạng thái, Location, ETag, lọc, events, report/Accept)."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.negotiation import negotiate
from app.models import Experiment, ExperimentEvent, Lab, User
from app.models.database import now_iso
from app.seed import ensure_labs

API = "/api/v1"
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


def run_one(client, app, headers, payload=PAYLOAD, lab="api_lab") -> str:
    r = client.post(f"{API}/labs/{lab}/experiments", json=payload, headers=headers)
    assert r.status_code == 202, r.text
    assert app.state.orchestrator.wait_idle(10)
    return r.json()["id"]


def insert_exp(app, lab_code="api_lab", state="queued", **over) -> str:
    with app.state.session_factory() as db:
        user = db.scalar(select(User))
        lab = db.scalar(select(Lab).where(Lab.code == lab_code))
        exp = Experiment(user_id=user.id, lab_id=lab.id, state=state, input_data={"scenario_code": "x"}, **over)
        db.add(exp)
        db.commit()
        return exp.id


# ---- xác thực ----

def test_all_endpoints_require_auth(client, seeded):
    assert client.post(f"{API}/labs/api_lab/experiments", json=PAYLOAD).status_code == 401
    for path in ("/experiments", "/experiments/exp_x", "/experiments/exp_x/events", "/experiments/exp_x/report"):
        assert client.get(API + path).status_code == 401, path


# ---- tạo ----

def test_create_returns_202_location_retry_after(client, app, h):
    r = client.post(f"{API}/labs/api_lab/experiments", json=PAYLOAD, headers=h)
    assert r.status_code == 202
    body = r.json()
    assert r.headers["location"] == f"/api/v1/experiments/{body['id']}"
    assert r.headers["retry-after"] == "2"
    assert body["id"].startswith("exp_") and body["lab"] == "api_lab"
    assert body["state"] == "queued" and body["result"] is None and body["error"] is None
    assert body["result_kind"] is None and body["duration_ms"] is None
    assert body["config"]["scenario_code"] == "burst-30"
    assert "rate_limit_modes" in body["config"]  # config là payload đã chuẩn hóa (mặc định được điền)
    assert body["links"]["events"].endswith(f"{body['id']}/events")
    app.state.orchestrator.wait_idle(10)


def test_create_errors(client, app, h):
    url = f"{API}/labs/api_lab/experiments"
    assert client.post(f"{API}/labs/zzz/experiments", json=PAYLOAD, headers=h).status_code == 404
    r = client.post(url, json={"scenario_code": "http://evil/x"}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"
    assert r.headers["content-type"].startswith("application/problem+json")
    assert client.post(url, json={**PAYLOAD, "url": "http://x"}, headers=h).status_code == 422  # khóa lạ
    assert client.post(url, json=["a"], headers=h).status_code == 422  # không phải đối tượng
    r = client.post(url, content=b"{not json", headers={**h, "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["code"] == "MALFORMED_REQUEST"
    r = client.post(url, headers=h)  # thiếu thân
    assert r.status_code == 400 and r.json()["code"] == "MALFORMED_REQUEST"
    with app.state.session_factory() as db:  # lỗi không tạo experiment
        assert db.scalar(select(func.count()).select_from(Experiment)) == 0


def test_disabled_lab_is_409_before_validation(client, h):
    r = client.post(f"{API}/labs/poisoning/experiments", json={"bad": "payload"}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "LAB_DISABLED"


# ---- đọc một thử nghiệm + ETag ----

def test_get_done_experiment_and_etag(client, app, h):
    exp_id = run_one(client, app, h)
    r = client.get(f"{API}/experiments/{exp_id}", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "done" and body["result_kind"] in ("real", "simulated", "planned")
    assert body["result"]["limitations"], "kết quả done phải có limitations"
    assert {"summary", "components", "metrics", "artifacts", "limitations"} <= set(body["result"])
    assert isinstance(body["duration_ms"], int) and body["duration_ms"] >= 0
    assert body["started_at"] and body["finished_at"] and body["error"] is None
    etag = r.headers["etag"]
    assert etag.startswith('"')
    r304 = client.get(f"{API}/experiments/{exp_id}", headers={**h, "If-None-Match": etag})
    assert r304.status_code == 304 and r304.content == b"" and r304.headers["etag"] == etag
    assert client.get(f"{API}/experiments/{exp_id}", headers={**h, "If-None-Match": f"W/{etag}"}).status_code == 304
    assert client.get(f"{API}/experiments/{exp_id}", headers={**h, "If-None-Match": '"khac"'}).status_code == 200
    assert client.get(f"{API}/experiments/exp_nope", headers=h).status_code == 404


def test_etag_changes_when_state_changes(client, app, h):
    exp_id = insert_exp(app, state="queued")
    etag1 = client.get(f"{API}/experiments/{exp_id}", headers=h).headers["etag"]
    with app.state.session_factory() as db:
        db.get(Experiment, exp_id).state = "running"
        db.commit()
    r = client.get(f"{API}/experiments/{exp_id}", headers={**h, "If-None-Match": etag1})
    assert r.status_code == 200 and r.headers["etag"] != etag1


def test_failed_experiment_shape(client, app, h):
    exp_id = insert_exp(
        app, state="failed", error_code="JOB_TIMEOUT", error_message="Quá thời gian tối đa của Lab.",
        started_at="2026-11-19T03:00:00.000Z", finished_at="2026-11-19T03:00:02.500Z",
    )
    body = client.get(f"{API}/experiments/{exp_id}", headers=h).json()
    assert body["state"] == "failed" and body["result"] is None and body["result_kind"] is None
    assert body["error"] == {"code": "JOB_TIMEOUT", "message": "Quá thời gian tối đa của Lab."}
    assert body["duration_ms"] == 2500


# ---- danh sách ----

def test_list_shape_sort_and_pagination(client, app, h):
    ids = [run_one(client, app, h) for _ in range(3)]
    r = client.get(f"{API}/experiments", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "page", "page_size", "total"} and body["total"] == 3
    assert [i["id"] for i in body["items"]] == ids[::-1]  # mặc định -created_at
    asc = client.get(f"{API}/experiments", params={"sort": "created_at"}, headers=h).json()
    assert [i["id"] for i in asc["items"]] == ids
    p2 = client.get(f"{API}/experiments", params={"page": 2, "page_size": 2}, headers=h).json()
    assert [i["id"] for i in p2["items"]] == [ids[0]] and p2["total"] == 3 and p2["page"] == 2


def test_list_filters(client, app, h, h2):
    a = run_one(client, app, h)
    b = run_one(client, app, h2, {"scenario_code": "burst-30", "rate_limit_modes": [True]})
    failed = insert_exp(app, state="failed", error_code="INTERRUPTED", error_message="x")

    def ids(**params):
        r = client.get(f"{API}/experiments", params=params, headers=h)
        assert r.status_code == 200, r.text
        return {i["id"] for i in r.json()["items"]}

    assert ids() == {a, b, failed}
    assert ids(lab="api_lab") == {a, b, failed} and ids(lab="deepfake") == set()
    assert ids(state="done") == {a, b}
    assert ids(state=["done", "failed"]) == {a, b, failed}
    assert ids(result_kind="simulated") == {a, b} and ids(result_kind="real") == set()
    me = client.get(f"{API}/users/me", headers=h).json()["id"]
    assert ids(created_by="me") == {a, failed}  # insert_exp gán cho user đầu tiên (chính là người dùng h)
    assert b not in ids(created_by="me")
    assert ids(created_by=me) == ids(created_by="me")
    assert ids(created_from="2999-01-01T00:00:00Z") == set()
    assert ids(created_to="2000-01-01T00:00:00Z") == set()
    assert ids(created_from="2000-01-01T00:00:00+07:00", created_to="2999-01-01") == {a, b, failed}


@pytest.mark.parametrize(
    "params",
    [{"zzz": 1}, {"state": "bogus"}, {"result_kind": "x"}, {"sort": "state"}, {"created_from": "hôm qua"},
     {"page": 0}, {"page_size": 101}],
)
def test_list_rejects_bad_params(client, h, params):
    r = client.get(f"{API}/experiments", params=params, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"


# ---- events ----

def test_events_ordered_and_paginated(client, app, h):
    exp_id = run_one(client, app, h)
    r = client.get(f"{API}/experiments/{exp_id}/events", headers=h)
    assert r.status_code == 200
    body = r.json()
    types = [e["event_type"] for e in body["items"]]
    assert types[0] == "queued" and "job_started" in types and types[-1] == "job_finished"
    assert [e["id"] for e in body["items"]] == sorted(e["id"] for e in body["items"])
    assert set(body["items"][0]) == {"id", "level", "event_type", "message", "data", "created_at"}
    first = client.get(f"{API}/experiments/{exp_id}/events", params={"page_size": 1}, headers=h).json()
    assert first["total"] == body["total"] and len(first["items"]) == 1
    assert client.get(f"{API}/experiments/exp_nope/events", headers=h).status_code == 404
    assert client.get(f"{API}/experiments/{exp_id}/events", params={"x": 1}, headers=h).status_code == 422


# ---- report ----

def test_report_markdown_default_and_json(client, app, h):
    exp_id = run_one(client, app, h)
    url = f"{API}/experiments/{exp_id}/report"
    for accept in (None, "text/markdown", "*/*", "text/*", "text/html, text/markdown;q=0.5"):
        r = client.get(url, headers={**h, **({"Accept": accept} if accept else {})})
        assert r.status_code == 200, accept
        assert r.headers["content-type"] == "text/markdown; charset=utf-8"
        assert r.headers["vary"] == "Accept"
        assert exp_id in r.text and "## Giới hạn" in r.text and "## Nhật ký" in r.text
    r = client.get(url, headers={**h, "Accept": "application/json"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["experiment"]["id"] == exp_id and body["experiment"]["result"]["limitations"]
    assert body["events"][0]["event_type"] == "queued"
    r = client.get(url, headers={**h, "Accept": "application/xml"})
    assert r.status_code == 406 and r.json()["code"] == "NOT_ACCEPTABLE"
    assert client.get(f"{API}/experiments/exp_nope/report", headers=h).status_code == 404


def test_report_states(client, app, h):
    queued = insert_exp(app, state="queued")
    r = client.get(f"{API}/experiments/{queued}/report", headers=h)
    assert r.status_code == 409 and r.json()["code"] == "STATE_CONFLICT"
    failed = insert_exp(app, state="failed", error_code="RESULT_REJECTED", error_message="bị từ chối")
    r = client.get(f"{API}/experiments/{failed}/report", headers=h)
    assert r.status_code == 200 and "RESULT_REJECTED" in r.text and "## Tóm tắt" not in r.text


def test_report_escapes_table_cells(client, app, h):
    exp_id = insert_exp(app, state="failed", error_code="INTERNAL_ERROR", error_message="x")
    with app.state.session_factory() as db:
        db.add(ExperimentEvent(experiment_id=exp_id, level="info", event_type="note", message="a|b\nc"))
        db.commit()
    text = client.get(f"{API}/experiments/{exp_id}/report", headers=h).text
    assert "a\\|b c" in text


# ---- content negotiation ----

@pytest.mark.parametrize(
    "accept,expected",
    [
        (None, "text/markdown"),
        ("", "text/markdown"),
        ("*/*", "text/markdown"),
        ("application/json", "application/json"),
        ("application/json;q=0.2, text/markdown;q=0.9", "text/markdown"),
        ("text/markdown;q=0, */*", "application/json"),
        ("application/*", "application/json"),
        ("application/xml", None),
        ("text/html", None),
        ("application/json;q=0", None),
        ("rác;;q=x", None),
    ],
)
def test_negotiate(accept, expected):
    assert negotiate(accept, ("text/markdown", "application/json")) == expected
