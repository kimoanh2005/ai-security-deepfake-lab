"""Orchestrator (T-08): vòng đời, result_guard qua orchestrator, timeout, khởi động lại, records, đồng thời."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.core.errors import AppError
from app.jobs.orchestrator import Orchestrator
from app.main import create_app
from app.models import Experiment, ExperimentEvent, Lab, PromptCase, PromptRun, User
from app.models import LabResult as LabResultRow
from app.seed import ensure_labs
from app.services.base import Component, LabResult, LabValidationError, RunContext

API_PAYLOAD = {"scenario_code": "burst-30"}


# ---- tiện ích ----

class FakeService:
    """Service giả lập cho test; `fn(ctx, payload)` quyết định hành vi."""

    def __init__(self, fn, code="api_lab"):
        self.code = code
        self._fn = fn

    def validate(self, payload, settings):
        if not isinstance(payload, dict) or "bad" in payload:
            raise LabValidationError([{"field": "bad", "message": "không hợp lệ"}])
        return dict(payload)

    def run(self, ctx: RunContext, payload):
        return self._fn(ctx, payload)


def ok_result(**over) -> LabResult:
    base = LabResult(
        result_kind="simulated",
        summary="ok",
        components=[Component("sandbox", "simulated", "giả lập")],
        limitations=["giới hạn thử nghiệm"],
        metrics={"n": 1},
    )
    return replace(base, **over)


@pytest.fixture()
def user_id(app) -> str:
    with app.state.session_factory() as db:
        ensure_labs(db)
        user = User(email="runner@lab.local", password_hash="x", full_name="Runner")
        db.add(user)
        db.commit()
        return user.id


@pytest.fixture()
def make_orch(app, settings):
    created: list[Orchestrator] = []

    def _make(fn=None, services=None, **settings_over):
        cfg = settings.model_copy(update=settings_over) if settings_over else settings
        if services is None and fn is not None:
            services = {"api_lab": FakeService(fn)}
        kwargs = {"grace_seconds": 0.1}
        orch = Orchestrator(cfg, app.state.session_factory, services, **kwargs)
        created.append(orch)
        return orch

    yield _make
    for orch in created:
        orch.stop(timeout=1)


def snapshot(app, exp_id: str) -> SimpleNamespace:
    with app.state.session_factory() as db:
        return SimpleNamespace(
            exp=db.get(Experiment, exp_id),
            events=list(db.scalars(select(ExperimentEvent).where(ExperimentEvent.experiment_id == exp_id).order_by(ExperimentEvent.id))),
            result=db.scalar(select(LabResultRow).where(LabResultRow.experiment_id == exp_id)),
            runs=list(db.scalars(select(PromptRun).where(PromptRun.experiment_id == exp_id))),
        )


def run_one(app, orch, user_id, code="api_lab", payload=None, wait=10.0) -> SimpleNamespace:
    orch.start()
    exp = orch.submit(user_id, code, API_PAYLOAD if payload is None else payload)
    assert orch.wait_idle(wait), "job không kết thúc đúng hạn"
    return snapshot(app, exp.id)


def types(snap) -> list[str]:
    return [e.event_type for e in snap.events]


# ---- vòng đời ----

def test_lifecycle_queued_running_done_with_real_stub(app, make_orch, user_id):
    orch = make_orch()  # registry thật: stub api_lab
    orch.start()
    exp = orch.submit(user_id, "api_lab", API_PAYLOAD)
    assert exp.state == "queued" and exp.id.startswith("exp_")
    assert exp.input_data == {"scenario_code": "burst-30", "rate_limit_modes": [False, True]}  # đã chuẩn hóa
    assert orch.wait_idle()
    snap = snapshot(app, exp.id)
    assert snap.exp.state == "done"
    assert snap.exp.started_at and snap.exp.finished_at and snap.exp.error_code is None
    assert snap.exp.started_at <= snap.exp.finished_at
    assert types(snap) == ["queued", "job_started", "stub", "job_finished"]
    assert snap.result.guard_passed == 1 and snap.result.result_kind == "simulated"
    assert snap.result.limitations and snap.result.result_data["components"][0]["kind"] == "simulated"
    assert snap.result.guard_details["passed"] is True


def test_submit_before_start_then_start_runs_once(app, make_orch, user_id):
    orch = make_orch()
    exp = orch.submit(user_id, "api_lab", API_PAYLOAD)  # chưa có worker
    assert snapshot(app, exp.id).exp.state == "queued"
    orch.start()  # recover xếp lại job queued, trong khi submit cũng đã xếp: phải chạy đúng một lần
    assert orch.wait_idle()
    snap = snapshot(app, exp.id)
    assert snap.exp.state == "done" and types(snap).count("job_started") == 1


def test_start_is_idempotent(make_orch):
    orch = make_orch()
    orch.start()
    first = orch._thread
    orch.start()
    assert orch._thread is first and orch.health()["worker_alive"] is True


def test_only_one_job_runs_at_a_time(app, make_orch, user_id):
    lock, state = threading.Lock(), {"now": 0, "max": 0}

    def fn(ctx, payload):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return ok_result()

    orch = make_orch(fn)
    orch.start()
    ids = [orch.submit(user_id, "api_lab", API_PAYLOAD).id for _ in range(4)]
    assert orch.wait_idle()
    assert state["max"] == 1
    assert all(snapshot(app, i).exp.state == "done" for i in ids)


def test_concurrent_submits_all_finish_without_db_lock(app, make_orch, user_id):
    orch = make_orch()
    orch.start()
    with ThreadPoolExecutor(8) as pool:
        ids = list(pool.map(lambda _: orch.submit(user_id, "api_lab", API_PAYLOAD).id, range(16)))
    assert len(set(ids)) == 16
    assert orch.wait_idle(30)
    assert all(snapshot(app, i).exp.state == "done" for i in ids)


# ---- submit: lỗi trước khi tạo experiment ----

def count_experiments(app) -> int:
    with app.state.session_factory() as db:
        return db.scalar(select(func.count()).select_from(Experiment))


def test_submit_validation_error_is_422_and_creates_nothing(app, make_orch, user_id):
    orch = make_orch()
    with pytest.raises(AppError) as exc:
        orch.submit(user_id, "api_lab", {"scenario_code": "http://evil"})
    assert (exc.value.status, exc.value.code) == (422, "VALIDATION_ERROR")
    assert {e["field"] for e in exc.value.errors} == {"scenario_code"}
    assert count_experiments(app) == 0


def test_submit_unknown_lab_404(app, make_orch, user_id):
    with pytest.raises(AppError) as exc:
        make_orch().submit(user_id, "nope", {})
    assert (exc.value.status, exc.value.code) == (404, "NOT_FOUND")
    assert count_experiments(app) == 0


def test_submit_disabled_lab_409_before_validation(app, make_orch, user_id):
    orch = make_orch()
    for code in ("poisoning", "api_lab"):  # poisoning tắt sẵn trong catalog; api_lab tắt bằng admin
        if code == "api_lab":
            with app.state.session_factory() as db:
                db.scalar(select(Lab).where(Lab.code == "api_lab")).enabled = 0
                db.commit()
        with pytest.raises(AppError) as exc:
            orch.submit(user_id, code, {"bad": True})  # payload sai nhưng Lab tắt thì phải 409, không phải 422
        assert (exc.value.status, exc.value.code) == (409, "LAB_DISABLED")
    assert count_experiments(app) == 0


def test_submit_lab_without_service_503(app, make_orch, user_id):
    orch = make_orch(services={})
    with pytest.raises(AppError) as exc:
        orch.submit(user_id, "api_lab", API_PAYLOAD)
    assert (exc.value.status, exc.value.code) == (503, "DEP_UNAVAILABLE")
    assert count_experiments(app) == 0


# ---- lỗi của service ----

def test_service_exception_fails_job_without_leaking_details(app, make_orch, user_id):
    def fn(ctx, payload):
        ctx.emit("info", "request_sent", "đã gửi 3 request", {"n": 3})
        raise RuntimeError("token=SECRET-abc123 bị lộ")

    snap = run_one(app, make_orch(fn), user_id)
    assert snap.exp.state == "failed" and snap.exp.error_code == "INTERNAL_ERROR"
    assert snap.result is None
    assert types(snap) == ["queued", "job_started", "request_sent", "job_failed"]
    dump = repr((snap.exp.error_message, [(e.message, e.data_json) for e in snap.events]))
    assert "SECRET-abc123" not in dump
    assert snap.events[-1].data_json == {"exception": "RuntimeError"}


def test_worker_survives_failed_job(app, make_orch, user_id):
    calls = []

    def fn(ctx, payload):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("boom")
        return ok_result()

    orch = make_orch(fn)
    orch.start()
    first = orch.submit(user_id, "api_lab", API_PAYLOAD).id
    second = orch.submit(user_id, "api_lab", API_PAYLOAD).id
    assert orch.wait_idle()
    assert snapshot(app, first).exp.state == "failed"
    assert snapshot(app, second).exp.state == "done"


def test_unserializable_event_data_is_replaced(app, make_orch, user_id):
    def fn(ctx, payload):
        ctx.emit("info", "x", "a", {"obj": object()})
        ctx.emit("info", "y", "b", {"big": "z" * 10_000})
        ctx.emit("warn", "z", "c", {"ok": [1, 2]})
        return ok_result()

    snap = run_one(app, make_orch(fn), user_id)
    assert snap.exp.state == "done"
    by_type = {e.event_type: e.data_json for e in snap.events}
    assert "_dropped" in by_type["x"] and "_dropped" in by_type["y"] and by_type["z"] == {"ok": [1, 2]}


# ---- result_guard qua orchestrator (SPEC 11.1 mục 4) ----

@pytest.mark.parametrize(
    "result,failed_check",
    [
        (ok_result(result_kind="real", components=[Component("a", "real")]), "G2_within_lab_ceiling"),  # Lab simulated trả real
        (ok_result(limitations=[]), "G3_limitations"),
        (ok_result(metrics={"accuracy": 0.97}), "G4_no_misleading_metrics"),
        (ok_result(artifacts=["../../etc/passwd"]), "G6_artifacts_relative"),
        (None, "shape"),  # service quên return
    ],
)
def test_guard_rejection_fails_job(app, make_orch, user_id, result, failed_check):
    snap = run_one(app, make_orch(lambda ctx, payload: result), user_id)
    assert snap.exp.state == "failed" and snap.exp.error_code == "RESULT_REJECTED"
    assert failed_check in snap.exp.error_message
    assert types(snap)[-1] == "result_rejected"
    assert failed_check in snap.events[-1].data_json["failed"]
    assert snap.result.guard_passed == 0 and snap.result.result_data == {} and snap.result.limitations == []
    assert failed_check in snap.result.guard_details["checks"] and snap.result.guard_details["passed"] is False


def test_guard_ceiling_follows_lab_status(app, make_orch, user_id):
    with app.state.session_factory() as db:
        db.scalar(select(Lab).where(Lab.code == "api_lab")).status = "real"  # admin nâng trần
        db.commit()
    real = ok_result(result_kind="real", components=[Component("sandbox", "real")])
    snap = run_one(app, make_orch(lambda ctx, payload: real), user_id)
    assert snap.exp.state == "done" and snap.result.result_kind == "real"


def test_rejected_result_keeps_service_events(app, make_orch, user_id):
    def fn(ctx, payload):
        ctx.emit("warn", "rate_limited", "429")
        return ok_result(limitations=[])

    snap = run_one(app, make_orch(fn), user_id)
    assert types(snap) == ["queued", "job_started", "rate_limited", "result_rejected"]


# ---- timeout ----

def test_cooperative_timeout_fails_job_and_queue_continues(app, make_orch, user_id):
    def fn(ctx, payload):
        if payload.get("slow"):
            while True:
                ctx.check()
                time.sleep(0.01)
        return ok_result()

    orch = make_orch(fn, job_timeout_api_lab=0.2)
    orch.start()
    slow = orch.submit(user_id, "api_lab", {"slow": True}).id
    after = orch.submit(user_id, "api_lab", API_PAYLOAD).id
    assert orch.wait_idle()
    snap = snapshot(app, slow)
    assert snap.exp.state == "failed" and snap.exp.error_code == "JOB_TIMEOUT"
    assert types(snap)[-1] == "timeout" and snap.result is None
    assert snapshot(app, after).exp.state == "done"


def test_service_ignoring_check_is_abandoned_not_blocking(app, make_orch, user_id):
    def fn(ctx, payload):
        if payload.get("stuck"):
            time.sleep(1.5)  # không gọi ctx.check()
        return ok_result()

    orch = make_orch(fn, job_timeout_api_lab=0.1)
    orch.start()
    stuck = orch.submit(user_id, "api_lab", {"stuck": True}).id
    nxt = orch.submit(user_id, "api_lab", API_PAYLOAD).id
    start = time.monotonic()
    assert orch.wait_idle(5)
    assert time.monotonic() - start < 1.4  # không phải chờ thread bị bỏ rơi
    snap = snapshot(app, stuck)
    assert snap.exp.state == "failed" and snap.exp.error_code == "JOB_TIMEOUT"
    assert "ctx.check" in snap.events[-1].message
    assert snapshot(app, nxt).exp.state == "done"
    time.sleep(1.5)  # thread bị bỏ rơi chạy xong cũng không đổi trạng thái cuối
    assert snapshot(app, stuck).exp.state == "failed"


def test_job_timeout_per_lab_setting(settings):
    cfg = settings.model_copy(update={"job_timeout_default": 50, "job_timeout_deepfake": 300})
    assert cfg.job_timeout_for("deepfake") == 300
    assert cfg.job_timeout_for("api_lab") == 50  # chưa đặt riêng → mặc định
    assert cfg.job_timeout_for("lab_khong_co") == 50


# ---- khởi động lại (FR-EXP-04) ----

def test_restart_fails_running_jobs_and_requeues_queued(app, make_orch, user_id):
    base = dict(user_id=user_id, lab_id="lab_api_lab", input_data={"scenario_code": "burst-30", "rate_limit_modes": [False, True]})
    with app.state.session_factory() as db:
        stuck = Experiment(state="running", started_at="2026-11-18T01:00:00.000Z", **base)
        waiting = Experiment(state="queued", **base)
        db.add_all([stuck, waiting])
        db.commit()
        stuck_id, waiting_id = stuck.id, waiting.id

    orch = make_orch()
    orch.start()
    assert orch.wait_idle()

    snap = snapshot(app, stuck_id)
    assert snap.exp.state == "failed" and snap.exp.error_code == "INTERRUPTED" and snap.exp.finished_at
    assert types(snap) == ["interrupted"] and snap.result is None
    assert snapshot(app, waiting_id).exp.state == "done"  # job chưa chạy được xếp lại, không bị mất


def test_start_without_migrated_db_gives_clear_error(settings, tmp_path):
    fresh = create_app(settings.model_copy(update={"db_path": str(tmp_path / "empty.db")}))
    try:
        with pytest.raises(RuntimeError, match="alembic upgrade head"):
            Orchestrator(fresh.state.settings, fresh.state.session_factory).start()
    finally:
        fresh.state.engine.dispose()


def test_app_lifespan_starts_and_stops_worker(client):
    orch = client.app.state.orchestrator
    assert orch.health()["worker_alive"] is True
    assert set(orch.health()) == {"worker_alive", "queue_size", "busy"}  # không lộ id experiment


# ---- records → prompt_runs ----

def add_case(app, code="PI-01"):
    with app.state.session_factory() as db:
        case = PromptCase(code=code, task="tóm tắt", document_text="tài liệu giả", expected_behavior="chỉ tóm tắt")
        db.add(case)
        db.commit()
        return case.id


def pi_result(rows) -> LabResult:
    return ok_result(records={"prompt_runs": rows})


def test_records_are_written_with_case_id_in_one_transaction(app, make_orch, user_id):
    case_id = add_case(app)
    rows = [
        {"case_code": "PI-01", "attempt": 1, "defense": False, "response": "r1", "latency_ms": 120, "model_name": "m"},
        {"case_code": "PI-01", "attempt": 1, "defense": True, "response": "r2", "latency_ms": 90, "model_name": "m",
         "task_violated": False, "judged_by": "rule"},
    ]
    snap = run_one(app, make_orch(lambda ctx, p: pi_result(rows)), user_id, code="api_lab")
    assert snap.exp.state == "done"
    assert len(snap.runs) == 2
    by_defense = {r.defense: r for r in snap.runs}
    assert by_defense[0].case_id == case_id and by_defense[0].id.startswith("run_")
    assert by_defense[0].task_violated is None and by_defense[0].judged_by is None  # không tự gán thành công (FR-PI-05)
    assert by_defense[1].task_violated == 0 and by_defense[1].judged_by == "rule"
    assert {r.created_at for r in snap.runs}  # orchestrator tự thêm id, experiment_id, created_at


def test_unknown_case_code_rejects_and_writes_no_runs(app, make_orch, user_id):
    add_case(app, "PI-01")
    rows = [{"case_code": "PI-01", "defense": False}, {"case_code": "PI-99", "defense": True}]
    snap = run_one(app, make_orch(lambda ctx, p: pi_result(rows)), user_id)
    assert snap.exp.state == "failed" and snap.exp.error_code == "RESULT_REJECTED"
    assert snap.runs == []  # không ghi dở dang
    assert "records_case_code" in snap.result.guard_details["checks"]
    assert "PI-99" in snap.result.guard_details["checks"]["records_case_code"]["detail"]


@pytest.mark.parametrize(
    "row",
    [
        {"defense": True},  # thiếu case_code
        {"case_code": "PI-01"},  # thiếu defense
        {"case_code": "PI-01", "defense": "yes"},
        {"case_code": "PI-01", "defense": 2},
        {"case_code": "PI-01", "defense": True, "attempt": 0},
        {"case_code": "PI-01", "defense": True, "latency_ms": "nhanh"},
        {"case_code": "PI-01", "defense": True, "response": 5},
        {"case_code": "PI-01", "defense": True, "task_violated": True},  # tự chấm mà không có judged_by='rule'
        {"case_code": "PI-01", "defense": True, "task_violated": True, "judged_by": "human"},
    ],
)
def test_malformed_record_rows_are_rejected_by_guard(app, make_orch, user_id, row):
    add_case(app)
    snap = run_one(app, make_orch(lambda ctx, p: pi_result([row])), user_id)
    assert snap.exp.state == "failed" and snap.exp.error_code == "RESULT_REJECTED"
    assert "records" in snap.result.guard_details["checks"] and snap.runs == []


def test_records_for_unknown_table_rejected(app, make_orch, user_id):
    snap = run_one(app, make_orch(lambda ctx, p: ok_result(records={"users": [{"email": "a@b"}]})), user_id)
    assert snap.exp.state == "failed" and snap.exp.error_code == "RESULT_REJECTED"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1  # chỉ user của fixture
