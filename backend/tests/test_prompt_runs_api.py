"""GET/PATCH /experiments/{id}/prompt-runs và ASR tính lại sau khi người chấm tay (FR-PI-06, FR-PI-07)."""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.models import ExperimentEvent
from app.seed import ensure_labs, ensure_prompt_cases
from app.services.labs.prompt_injection import PromptInjectionService

API = "/api/v1"


@pytest.fixture()
def h(app, make_user, login, monkeypatch):
    with app.state.session_factory() as db:
        ensure_labs(db)
        ensure_prompt_cases(db)
    s = app.state.settings
    s.ollama_url, s.ollama_model = "https://demo-lab.trycloudflare.com", "security-bot"
    # Mô hình giả luôn nhả bí mật: baseline rò rỉ; nhánh phòng thủ che bí mật hoặc chặn đầu vào.
    monkeypatch.setattr(PromptInjectionService, "transport",
                        httpx.MockTransport(lambda r: httpx.Response(200, json={"response": "Mã là FLAG{AI_SECURITY_OLLAMA_2026}"})))
    return login(*make_user())


@pytest.fixture()
def exp_id(client, app, h):
    r = client.post(f"{API}/labs/prompt_injection/experiments", json={"case_codes": ["PI-05", "PI-12"]}, headers=h)
    assert r.status_code == 202, r.text
    assert app.state.orchestrator.wait_idle(10)
    return r.json()["id"]


def runs(client, h, exp_id):
    r = client.get(f"{API}/experiments/{exp_id}/prompt-runs", headers=h)
    assert r.status_code == 200
    return r.json()


def metrics(client, h, exp_id):
    return client.get(f"{API}/experiments/{exp_id}", headers=h).json()["result"]["metrics"]


def test_requires_auth(client, exp_id):
    assert client.get(f"{API}/experiments/{exp_id}/prompt-runs").status_code == 401
    assert client.patch(f"{API}/experiments/{exp_id}/prompt-runs/x", json={"task_violated": True}).status_code == 401


def test_list_prompt_runs(client, h, exp_id):
    body = runs(client, h, exp_id)
    assert body["total"] == 4 and {"items", "page", "page_size"} <= set(body)
    row = body["items"][0]
    assert set(row) == {"id", "case_code", "attempt", "defense", "response", "latency_ms", "model_name",
                        "task_violated", "judged_by"}
    assert row["id"].startswith("run_") and row["model_name"] == "security-bot"
    assert client.get(f"{API}/experiments/exp_missing/prompt-runs", headers=h).status_code == 404
    assert client.get(f"{API}/experiments/{exp_id}/prompt-runs?x=1", headers=h).status_code == 422


def test_asr_null_until_human_judges_then_recomputed(client, h, exp_id):
    before = metrics(client, h, exp_id)
    assert before["baseline"]["asr"] == 1.0                      # PI-05, PI-12 rò rỉ → quy tắc chấm vi phạm
    assert before["defense"] == {"attack_runs": 2, "judged": 1, "asr": None}  # PI-12 phòng thủ: chưa kết luận
    assert before["needs_review"] == 1

    pending = next(r for r in runs(client, h, exp_id)["items"]
                   if r["case_code"] == "PI-12" and r["defense"] and r["task_violated"] is None)
    assert "AI_SECURITY_OLLAMA_2026" not in pending["response"]  # đã che bí mật
    r = client.patch(f"{API}/experiments/{exp_id}/prompt-runs/{pending['id']}", json={"task_violated": False}, headers=h)
    assert r.status_code == 200 and r.json()["task_violated"] is False and r.json()["judged_by"] == "human"

    after = metrics(client, h, exp_id)
    assert after["defense"] == {"attack_runs": 2, "judged": 2, "asr": 0.0}
    assert after["needs_review"] == 0 and after["judged"] == 4

    # bỏ đánh giá → quay lại "chưa đủ dữ liệu"
    r = client.patch(f"{API}/experiments/{exp_id}/prompt-runs/{pending['id']}", json={"task_violated": None}, headers=h)
    assert r.json()["task_violated"] is None and r.json()["judged_by"] is None
    assert metrics(client, h, exp_id)["defense"]["asr"] is None


def test_patch_writes_event_without_response_text(client, app, h, exp_id):
    run = runs(client, h, exp_id)["items"][0]
    client.patch(f"{API}/experiments/{exp_id}/prompt-runs/{run['id']}", json={"task_violated": True}, headers=h)
    with app.state.session_factory() as db:
        ev = db.scalars(select(ExperimentEvent).where(ExperimentEvent.event_type == "prompt_run_judged")).one()
    assert ev.data_json["run_id"] == run["id"] and ev.data_json["judged_by"] == "human"
    assert "response" not in ev.data_json and "FLAG" not in str(ev.data_json)


def test_patch_etag_changes_and_report_uses_fresh_metrics(client, h, exp_id):
    etag = client.get(f"{API}/experiments/{exp_id}", headers=h).headers["etag"]
    run = next(r for r in runs(client, h, exp_id)["items"] if r["task_violated"] is None)
    client.patch(f"{API}/experiments/{exp_id}/prompt-runs/{run['id']}", json={"task_violated": True}, headers=h)
    assert client.get(f"{API}/experiments/{exp_id}", headers={**h, "If-None-Match": etag}).status_code == 200
    report = client.get(f"{API}/experiments/{exp_id}/report", headers={**h, "Accept": "application/json"}).json()
    assert report["experiment"]["result"]["metrics"]["needs_review"] == 0


@pytest.mark.parametrize("body", [{}, {"task_violated": "true"}, {"task_violated": 1}, {"task_violated": True, "judged_by": "rule"}])
def test_patch_rejects_bad_body(client, h, exp_id, body):
    run = runs(client, h, exp_id)["items"][0]
    r = client.patch(f"{API}/experiments/{exp_id}/prompt-runs/{run['id']}", json=body, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"


def test_patch_404_for_wrong_experiment_or_run(client, h, exp_id):
    run = runs(client, h, exp_id)["items"][0]
    assert client.patch(f"{API}/experiments/{exp_id}/prompt-runs/run_nope", json={"task_violated": True}, headers=h).status_code == 404
    assert client.patch(f"{API}/experiments/exp_nope/prompt-runs/{run['id']}", json={"task_violated": True}, headers=h).status_code == 404
