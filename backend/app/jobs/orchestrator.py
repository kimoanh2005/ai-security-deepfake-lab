"""Orchestrator (T-08): hàng đợi, một worker thread và vòng đời experiment (SPEC 5.2, 5.3).

    submit()  : kiểm tra Lab + service.validate → tạo experiment `queued` → xếp hàng (API trả 202)
    worker    : queued → running → service.run(ctx, payload) → result_guard → done | failed
    start()   : job `running` còn sót từ lần chạy trước → `failed` (INTERRUPTED); job `queued` được xếp lại

Quy tắc (SPEC 5.4): chỉ module này ghi `experiments`, `experiment_events`, `lab_results` và các bảng nghiệp vụ
(`prompt_runs`). Service không chạm DB; dữ liệu nghiệp vụ đi qua `LabResult.records`. Kết quả + records + events
của một job được ghi trong MỘT giao dịch cùng với việc chuyển trạng thái cuối.

Mã lỗi của job (`experiments.error_code`), khác mã lỗi HTTP:
    JOB_TIMEOUT      quá thời gian tối đa của Lab (FR-EXP-05)
    RESULT_REJECTED  result_guard (hoặc kiểm tra records) từ chối kết quả (FR-EXP-06); lý do ở lab_results.guard_details
    INTERRUPTED      máy chủ khởi động lại khi job đang chạy (FR-EXP-04)
    INTERNAL_ERROR   service ném ngoại lệ không lường trước hoặc lỗi ghi DB; chi tiết chỉ ghi log, không trả client

Hết giờ: orchestrator gọi `ctx.cancel()` để `ctx.check()` của service ném JobCancelled. Một service không bao giờ gọi
`ctx.check()` thì không dừng được (Python không giết được thread): job vẫn bị đánh dấu `failed` sau thời gian ân hạn
và thread bị bỏ rơi (daemon) để hàng đợi không kẹt. Vì vậy service dài PHẢI gọi `ctx.check()` định kỳ.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.core.errors import AppError
from app.models import Experiment, ExperimentEvent, Lab, PromptCase, PromptRun
from app.models import LabResult as LabResultRow  # bảng lab_results; LabResult của service là dataclass khác
from app.models.database import now_iso
from app.services.base import JobCancelled, LabService, LabValidationError, RunContext
from app.services.guard import run_guard
from app.services.registry import SERVICES

log = logging.getLogger("app.jobs")

GRACE_SECONDS = 5.0  # thời gian chờ thêm sau khi báo hủy, trước khi bỏ rơi thread của service
MAX_EVENT_DATA_BYTES = 4096  # `data` của một event lớn hơn mức này bị thay bằng ghi chú (chống phình CSDL)
_LEVELS = ("info", "warn", "error")


def _json_safe(data: Any) -> Any:
    """Chuẩn hóa `data` của event thành JSON thuần, hoặc ghi chú thay thế nếu không tuần tự hóa được/quá lớn."""
    if data is None:
        return None
    try:
        text = json.dumps(data, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return {"_dropped": "dữ liệu không tuần tự hóa được thành JSON"}
    if len(text.encode("utf-8")) > MAX_EVENT_DATA_BYTES:
        return {"_dropped": f"dữ liệu vượt {MAX_EVENT_DATA_BYTES} byte"}
    return json.loads(text)


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        services: Mapping[str, LabService] | None = None,
        grace_seconds: float = GRACE_SECONDS,
    ) -> None:
        self._settings = settings
        self._sf = session_factory
        self._services = SERVICES if services is None else services
        self._grace = grace_seconds
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._busy = False

    # ---- vòng đời của orchestrator ----

    def start(self) -> None:
        """Khôi phục sau khi khởi động lại rồi chạy worker. Gọi nhiều lần vô hại."""
        if self._thread is not None and self._thread.is_alive():
            return
        try:
            queued = self._recover()
        except OperationalError as exc:
            raise RuntimeError("CSDL chưa có bảng. Chạy trước: python -m alembic upgrade head") from exc
        for exp_id in queued:
            self._queue.put(exp_id)
        self._thread = threading.Thread(target=self._loop, name="lab-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Dừng worker (job đang chạy, nếu có, được chờ tối đa `timeout` giây; thread con là daemon)."""
        thread = self._thread
        if thread is None or not thread.is_alive():
            return
        self._queue.put(None)
        thread.join(timeout)

    def health(self) -> dict[str, Any]:
        """Dùng cho GET /health (T-10). Không lộ định danh experiment."""
        alive = self._thread is not None and self._thread.is_alive()
        return {"worker_alive": alive, "queue_size": self._queue.qsize(), "busy": self._busy}

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """Chờ hàng đợi rỗng và worker rảnh (chủ yếu cho test). Trả False nếu hết thời gian."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    # ---- nhận yêu cầu (gọi từ router, SPEC 5.3 bước 2–3) ----

    def submit(self, user_id: str, lab_code: str, payload: Any) -> Experiment:
        """Tạo experiment `queued` và xếp hàng. Lỗi → AppError (không tạo experiment):
        404 NOT_FOUND (không có Lab), 409 LAB_DISABLED, 422 VALIDATION_ERROR (LabValidationError), 503 DEP_UNAVAILABLE."""
        with self._sf() as db:
            lab = db.scalar(select(Lab).where(Lab.code == lab_code))
            if lab is None:
                raise AppError(404, "NOT_FOUND", "Không tìm thấy Lab.")
            if not lab.enabled:
                raise AppError(409, "LAB_DISABLED", "Lab đang tắt.")
            service = self._services.get(lab.service_key)
            if service is None:
                raise AppError(503, "DEP_UNAVAILABLE", "Lab chưa có service cài đặt.")
            try:
                config = service.validate(payload, self._settings)
            except LabValidationError as exc:
                raise AppError(422, "VALIDATION_ERROR", "Dữ liệu gửi lên không hợp lệ.", errors=exc.errors) from exc
            exp = Experiment(user_id=user_id, lab_id=lab.id, state="queued", input_data=config)
            db.add(exp)
            db.flush()
            db.add(ExperimentEvent(experiment_id=exp.id, level="info", event_type="queued", message="Đã xếp hàng."))
            db.commit()
        self._queue.put(exp.id)
        return exp

    # ---- worker ----

    def _loop(self) -> None:
        while True:
            exp_id = self._queue.get()
            try:
                if exp_id is None:
                    return
                self._process(exp_id)
            except Exception:  # worker không bao giờ được chết vì một job
                log.exception("Worker gặp lỗi không lường khi xử lý %s", exp_id)
            finally:
                self._busy = False
                self._queue.task_done()

    def _process(self, exp_id: str) -> None:
        with self._sf() as db:
            exp = db.get(Experiment, exp_id)
            if exp is None or exp.state != "queued":  # đã xử lý (xếp hàng trùng) hoặc bị xóa
                return
            lab = db.get(Lab, exp.lab_id)
            lab_code, ceiling, service_key = lab.code, lab.status, lab.service_key
            config = dict(exp.input_data)
            exp.state = "running"
            exp.started_at = now_iso()
            db.add(ExperimentEvent(experiment_id=exp_id, level="info", event_type="job_started", message="Bắt đầu chạy."))
            db.commit()
        self._busy = True
        try:
            self._execute(exp_id, lab_code, ceiling, service_key, config)
        except Exception:
            log.exception("Lỗi khi hoàn tất job %s", exp_id)
            self._fail(exp_id, "INTERNAL_ERROR", "Lỗi nội bộ khi xử lý job.", "job_failed")

    def _execute(self, exp_id: str, lab_code: str, ceiling: str, service_key: str, config: dict[str, Any]) -> None:
        service = self._services.get(service_key)
        if service is None:
            log.error("Lab %s trỏ tới service_key %r không có trong registry", lab_code, service_key)
            self._fail(exp_id, "INTERNAL_ERROR", "Lab chưa có service cài đặt.", "job_failed")
            return
        timeout = self._settings.job_timeout_for(lab_code)
        ctx = RunContext(self._settings, exp_id, Path(self._settings.storage_path), timeout)
        outcome, value = self._run_service(service, ctx, config, timeout)
        events = list(ctx.events)

        if outcome == "ok":
            self._finish(exp_id, ceiling, value, events)
        elif outcome in ("timeout", "abandoned"):
            note = f"Quá thời gian tối đa {timeout:g} giây."
            if outcome == "abandoned":
                log.warning("Job %s quá hạn và không dừng khi được báo hủy; thread bị bỏ rơi.", exp_id)
                note += " Service không dừng sau khi được báo hủy (thiếu ctx.check()?)."
            self._fail(exp_id, "JOB_TIMEOUT", "Quá thời gian tối đa của Lab.", "timeout", events, note=note)
        else:
            log.error("Service %s ném ngoại lệ ở job %s", service_key, exp_id, exc_info=value)
            self._fail(
                exp_id, "INTERNAL_ERROR", "Lab gặp lỗi khi chạy.", "job_failed", events,
                note="Lab gặp lỗi khi chạy.", data={"exception": type(value).__name__},
            )

    def _run_service(self, service: LabService, ctx: RunContext, config: dict[str, Any], timeout: float):
        """Chạy service trong thread con. Trả ("ok", LabResult) | ("timeout", None) | ("abandoned", None) | ("error", exc)."""
        holder: dict[str, Any] = {}

        def target() -> None:
            try:
                holder["result"] = service.run(ctx, config)
            except JobCancelled:
                holder["cancelled"] = True
            except BaseException as exc:  # noqa: BLE001 - mọi lỗi của service đều phải thành `failed`
                holder["error"] = exc

        runner = threading.Thread(target=target, name=f"lab-run-{ctx.experiment_id}", daemon=True)
        runner.start()
        runner.join(timeout)
        if runner.is_alive():
            ctx.cancel()
            runner.join(self._grace)
            return ("abandoned", None) if runner.is_alive() else ("timeout", None)
        if holder.get("cancelled"):
            return "timeout", None
        if "error" in holder:
            return "error", holder["error"]
        return "ok", holder.get("result")

    def _resolve_case_ids(self, records: dict[str, list[dict[str, Any]]]) -> tuple[str, dict[str, str]]:
        """Đổi case_code → case_id (HANDOFF: quy ước records). Trả (lỗi, {code: id}); lỗi rỗng nếu ổn."""
        codes = {row["case_code"] for row in records.get("prompt_runs", [])}
        if not codes:
            return "", {}
        with self._sf() as db:
            found = dict(db.execute(select(PromptCase.code, PromptCase.id).where(PromptCase.code.in_(codes))).all())
        missing = sorted(codes - set(found))
        if missing:
            return f"case_code không có trong prompt_cases: {missing[:5]}", {}
        return "", found

    def _finish(self, exp_id: str, ceiling: str, result: Any, events: list[dict[str, Any]]) -> None:
        """Cổng 3 rồi ghi kết quả (SPEC 5.3 bước 5). Đạt → done; không đạt → failed + result_rejected."""
        report = run_guard(result, ceiling, self._settings)
        case_ids: dict[str, str] = {}
        if report.passed:
            problem, case_ids = self._resolve_case_ids(result.records)
            if problem:
                report.add("records_case_code", False, problem)

        with self._sf() as db:
            exp = db.get(Experiment, exp_id)
            self._add_events(db, exp_id, events)
            if report.passed:
                db.add(
                    LabResultRow(
                        experiment_id=exp_id,
                        result_kind=result.result_kind,
                        result_data={
                            "summary": result.summary,
                            "components": [
                                {"name": c.name, "kind": c.kind, "note": getattr(c, "note", None)} for c in result.components
                            ],
                            "metrics": result.metrics,
                            "artifacts": list(result.artifacts),
                        },
                        limitations=list(result.limitations),
                        guard_passed=1,
                        guard_details=report.as_dict(),
                    )
                )
                for row in result.records.get("prompt_runs", []):
                    violated = row.get("task_violated")
                    db.add(
                        PromptRun(
                            experiment_id=exp_id,
                            case_id=case_ids[row["case_code"]],
                            attempt=row.get("attempt", 1),
                            defense=int(row["defense"]),
                            response=row.get("response"),
                            latency_ms=row.get("latency_ms"),
                            model_name=row.get("model_name"),
                            task_violated=None if violated is None else int(violated),
                            judged_by=row.get("judged_by"),
                        )
                    )
                exp.state = "done"
                db.add(
                    ExperimentEvent(
                        experiment_id=exp_id, level="info", event_type="job_finished", message="Hoàn tất.",
                        data_json={"result_kind": result.result_kind},
                    )
                )
            else:
                # Giữ bản ghi từ chối để truy vết (SPEC 6.1). Không lưu nội dung kết quả bị từ chối; `planned` là nhãn
                # yếu nhất, chỉ để thỏa ràng buộc cột — experiment trả `result: null` khi `failed`.
                db.add(
                    LabResultRow(
                        experiment_id=exp_id, result_kind="planned", result_data={}, limitations=[],
                        guard_passed=0, guard_details=report.as_dict(),
                    )
                )
                exp.state = "failed"
                exp.error_code = "RESULT_REJECTED"
                exp.error_message = "Kết quả của Lab bị từ chối: " + ", ".join(sorted(report.failed))
                db.add(
                    ExperimentEvent(
                        experiment_id=exp_id, level="error", event_type="result_rejected",
                        message="Kết quả của Lab bị từ chối.", data_json={"failed": report.failed},
                    )
                )
            exp.finished_at = now_iso()
            db.commit()

    def _fail(
        self,
        exp_id: str,
        code: str,
        message: str,
        event_type: str,
        events: list[dict[str, Any]] | None = None,
        *,
        note: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        with self._sf() as db:
            exp = db.get(Experiment, exp_id)
            if exp is None or exp.state in ("done", "failed"):
                return
            exp.state = "failed"
            exp.error_code = code
            exp.error_message = message
            exp.finished_at = now_iso()
            self._add_events(db, exp_id, events or [])
            db.add(
                ExperimentEvent(
                    experiment_id=exp_id, level="error", event_type=event_type, message=note or message,
                    data_json=_json_safe(data),
                )
            )
            db.commit()

    @staticmethod
    def _add_events(db: Session, exp_id: str, events: list[dict[str, Any]]) -> None:
        for e in events:
            level = e.get("level")
            db.add(
                ExperimentEvent(
                    experiment_id=exp_id,
                    level=level if level in _LEVELS else "info",
                    event_type=str(e.get("event_type", "event"))[:64],
                    message=str(e.get("message", ""))[:500],
                    data_json=_json_safe(e.get("data")),
                )
            )

    def _recover(self) -> list[str]:
        """FR-EXP-04: `running` còn sót → `failed` (INTERRUPTED). Trả id các job `queued` cũ để xếp hàng lại."""
        with self._sf() as db:
            now = now_iso()
            for exp in db.scalars(select(Experiment).where(Experiment.state == "running")):
                exp.state = "failed"
                exp.error_code = "INTERRUPTED"
                exp.error_message = "Máy chủ khởi động lại khi job đang chạy."
                exp.finished_at = now
                db.add(
                    ExperimentEvent(
                        experiment_id=exp.id, level="error", event_type="interrupted",
                        message="Máy chủ khởi động lại khi job đang chạy.",
                    )
                )
            queued = list(
                db.scalars(select(Experiment.id).where(Experiment.state == "queued").order_by(Experiment.created_at, Experiment.id))
            )
            db.commit()
        return queued
