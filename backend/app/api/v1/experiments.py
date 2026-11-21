"""T-09: POST /labs/{lab}/experiments, GET /experiments, /{id} (ETag), /{id}/events, /{id}/report (Accept).

Router chỉ đọc/ghi qua orchestrator (tạo) và truy vấn đọc (SPEC 5.4: chỉ orchestrator ghi experiments/events/results).
Mọi người dùng đã đăng nhập xem được thử nghiệm của cả nhóm (API-01 mục 7).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.etag import etag_of as _etag, if_none_match as _matches
from app.core.negotiation import negotiate
from app.core.query import PageParams, collection, reject_unknown_query
from app.deps import get_current_user, get_db, get_orchestrator
from app.models import Experiment, ExperimentEvent, Lab, User
from app.models import LabResult as LabResultRow

router = APIRouter(tags=["experiments"])

RETRY_AFTER_SECONDS = "2"  # gợi ý poll (API-01 5.5)
STATES = ("queued", "running", "done", "failed")
RESULT_KINDS = ("real", "simulated", "planned")
SORTS = {"created_at": Experiment.created_at, "-created_at": Experiment.created_at}
REPORT_TYPES = ("text/markdown", "application/json")


# ---- biểu diễn ----

def _parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _duration_ms(started: str | None, finished: str | None) -> int | None:
    if not started or not finished:
        return None
    a, b = _parse_iso(started), _parse_iso(finished)
    if a is None or b is None:
        return None
    return max(0, round((b - a).total_seconds() * 1000))


def experiment_out(exp: Experiment, lab_code: str, result_row: LabResultRow | None = None) -> dict[str, Any]:
    """Biểu diễn experiment (API-01 5.5, 6.1). `result`/`result_kind` chỉ có khi `done`; `failed` → `result: null` + `error`."""
    done = exp.state == "done" and result_row is not None and result_row.guard_passed == 1
    result = None
    if done:
        result = {**result_row.result_data, "limitations": list(result_row.limitations)}
    base = f"/api/v1/experiments/{exp.id}"
    return {
        "id": exp.id,
        "lab": lab_code,
        "state": exp.state,
        "result_kind": result_row.result_kind if done else None,
        "config": exp.input_data,
        "created_by": exp.user_id,
        "created_at": exp.created_at,
        "started_at": exp.started_at,
        "finished_at": exp.finished_at,
        "duration_ms": _duration_ms(exp.started_at, exp.finished_at),
        "result": result,
        "error": {"code": exp.error_code, "message": exp.error_message} if exp.state == "failed" else None,
        # Chỉ liệt kê tài nguyên con đã có thật (prompt_runs, artifacts thêm khi endpoint tồn tại).
        "links": {"self": base, "events": f"{base}/events", "report": f"{base}/report"},
    }


def event_out(ev: ExperimentEvent) -> dict[str, Any]:
    return {
        "id": ev.id,
        "level": ev.level,
        "event_type": ev.event_type,
        "message": ev.message,
        "data": ev.data_json,
        "created_at": ev.created_at,
    }


def _load(db: Session, exp_id: str) -> tuple[Experiment, str, LabResultRow | None]:
    exp = db.get(Experiment, exp_id)
    if exp is None:
        raise AppError(404, "NOT_FOUND", "Không tìm thấy thử nghiệm.")
    lab_code = db.scalar(select(Lab.code).where(Lab.id == exp.lab_id))
    result_row = db.scalar(select(LabResultRow).where(LabResultRow.experiment_id == exp.id))
    return exp, lab_code, result_row


# ---- tạo ----

@router.post("/labs/{code}/experiments", status_code=202, operation_id="create_experiment")
def create_experiment(
    code: str,
    response: Response,
    payload: Any = Body(...),  # thân khác nhau theo Lab; LabService.validate quyết định (API-01 mục 5)
    user: User = Depends(get_current_user),
    orchestrator=Depends(get_orchestrator),
    db: Session = Depends(get_db),
):
    # submit(): 404 (Lab), 409 LAB_DISABLED (trước validate), 422 (validate), 503 (chưa có service)
    exp = orchestrator.submit(user.id, code, payload)
    response.headers["Location"] = f"/api/v1/experiments/{exp.id}"
    response.headers["Retry-After"] = RETRY_AFTER_SECONDS
    return experiment_out(exp, code)


# ---- danh sách ----

def _to_utc_iso(value: str, field: str) -> str:
    dt = _parse_iso(value)
    if dt is None:
        raise AppError(422, "VALIDATION_ERROR", "Dữ liệu gửi lên không hợp lệ.",
                       errors=[{"field": field, "message": "Cần thời điểm ISO-8601."}])
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


@router.get("/experiments", operation_id="list_experiments")
def list_experiments(
    request: Request,
    params: PageParams = Depends(),
    lab: str | None = Query(None),
    state: list[str] | None = Query(None),
    result_kind: str | None = Query(None),
    created_by: str | None = Query(None),
    created_from: str | None = Query(None),
    created_to: str | None = Query(None),
    sort: str = Query("-created_at"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    reject_unknown_query(
        request,
        {"page", "page_size", "lab", "state", "result_kind", "created_by", "created_from", "created_to", "sort"},
    )
    errors: list[dict[str, str]] = []
    if state and any(s not in STATES for s in state):
        errors.append({"field": "state", "message": f"Giá trị hợp lệ: {', '.join(STATES)}."})
    if result_kind is not None and result_kind not in RESULT_KINDS:
        errors.append({"field": "result_kind", "message": f"Giá trị hợp lệ: {', '.join(RESULT_KINDS)}."})
    if sort not in SORTS:
        errors.append({"field": "sort", "message": f"Giá trị hợp lệ: {', '.join(SORTS)}."})
    lo = hi = None
    if created_from is not None:
        try:
            lo = _to_utc_iso(created_from, "created_from")
        except AppError as exc:
            errors.extend(exc.errors or [])
    if created_to is not None:
        try:
            hi = _to_utc_iso(created_to, "created_to")
        except AppError as exc:
            errors.extend(exc.errors or [])
    if errors:
        raise AppError(422, "VALIDATION_ERROR", "Tham số truy vấn không hợp lệ.", errors=errors)

    stmt = select(Experiment, Lab.code).join(Lab, Lab.id == Experiment.lab_id)
    if lab:
        stmt = stmt.where(Lab.code == lab)
    if state:
        stmt = stmt.where(Experiment.state.in_(state))
    if result_kind:
        # Nhãn chỉ có ở kết quả đã qua cổng 3 (done); kết quả bị từ chối không tính.
        stmt = stmt.join(LabResultRow, LabResultRow.experiment_id == Experiment.id).where(
            LabResultRow.guard_passed == 1, LabResultRow.result_kind == result_kind
        )
    if created_by:
        stmt = stmt.where(Experiment.user_id == (user.id if created_by == "me" else created_by))
    if lo:
        stmt = stmt.where(Experiment.created_at >= lo)
    if hi:
        stmt = stmt.where(Experiment.created_at <= hi)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = SORTS[sort]
    order = (column.desc(), Experiment.id.desc()) if sort.startswith("-") else (column.asc(), Experiment.id.asc())
    rows = db.execute(stmt.order_by(*order).offset(params.offset).limit(params.page_size)).all()

    ids = [exp.id for exp, _ in rows]
    results = {}
    if ids:
        results = {r.experiment_id: r for r in db.scalars(select(LabResultRow).where(LabResultRow.experiment_id.in_(ids)))}
    return collection([experiment_out(exp, code, results.get(exp.id)) for exp, code in rows], params, total)


# ---- một thử nghiệm ----

@router.get("/experiments/{exp_id}", operation_id="get_experiment")
def get_experiment(
    exp_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    exp, lab_code, result_row = _load(db, exp_id)
    body = experiment_out(exp, lab_code, result_row)
    etag = _etag(body)
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if _matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return body


@router.get("/experiments/{exp_id}/events", operation_id="list_experiment_events")
def list_experiment_events(
    exp_id: str,
    request: Request,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    reject_unknown_query(request, {"page", "page_size"})
    _load(db, exp_id)
    total = db.scalar(select(func.count()).select_from(ExperimentEvent).where(ExperimentEvent.experiment_id == exp_id)) or 0
    rows = db.scalars(
        select(ExperimentEvent)
        .where(ExperimentEvent.experiment_id == exp_id)
        .order_by(ExperimentEvent.id)
        .offset(params.offset)
        .limit(params.page_size)
    )
    return collection([event_out(r) for r in rows], params, total)


# ---- báo cáo ----

def _md_cell(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def render_markdown(exp: dict[str, Any], events: list[dict[str, Any]]) -> str:
    result = exp["result"]
    lines = [
        f"# Báo cáo thử nghiệm {exp['id']}",
        "",
        "| Trường | Giá trị |",
        "|---|---|",
        f"| Lab | {_md_cell(exp['lab'])} |",
        f"| Trạng thái | {_md_cell(exp['state'])} |",
        f"| Nhãn kết quả | {_md_cell(exp['result_kind'])} |",
        f"| Tạo lúc | {_md_cell(exp['created_at'])} |",
        f"| Bắt đầu | {_md_cell(exp['started_at'])} |",
        f"| Kết thúc | {_md_cell(exp['finished_at'])} |",
        f"| Thời lượng (ms) | {_md_cell(exp['duration_ms'])} |",
        "",
        "## Cấu hình",
        "",
        "```json",
        json.dumps(exp["config"], ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    if result:
        lines += ["## Tóm tắt", "", str(result.get("summary", "")), "", "## Thành phần và nhãn", ""]
        lines += ["| Thành phần | Nhãn | Ghi chú |", "|---|---|---|"]
        for c in result.get("components", []):
            lines.append(f"| {_md_cell(c.get('name'))} | {_md_cell(c.get('kind'))} | {_md_cell(c.get('note'))} |")
        lines += ["", "## Chỉ số", "", "```json", json.dumps(result.get("metrics", {}), ensure_ascii=False, indent=2), "```", ""]
        lines += ["## Giới hạn", ""] + [f"- {item}" for item in result.get("limitations", [])] + [""]
    if exp["error"]:
        lines += ["## Lỗi", "", f"- Mã: `{exp['error']['code']}`", f"- Thông báo: {exp['error']['message']}", ""]
    lines += ["## Nhật ký", "", "| # | Mức | Loại | Thông điệp | Lúc |", "|---|---|---|---|---|"]
    for e in events:
        lines.append(
            f"| {e['id']} | {_md_cell(e['level'])} | {_md_cell(e['event_type'])} | {_md_cell(e['message'])} | {_md_cell(e['created_at'])} |"
        )
    return "\n".join(lines) + "\n"


@router.get("/experiments/{exp_id}/report", operation_id="get_experiment_report")
def get_experiment_report(
    exp_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    chosen = negotiate(request.headers.get("accept"), REPORT_TYPES)
    if chosen is None:
        raise AppError(406, "NOT_ACCEPTABLE", "Chỉ hỗ trợ Accept: text/markdown hoặc application/json.")
    exp, lab_code, result_row = _load(db, exp_id)
    if exp.state in ("queued", "running"):
        raise AppError(409, "STATE_CONFLICT", "Thử nghiệm chưa kết thúc nên chưa có báo cáo.")
    body = experiment_out(exp, lab_code, result_row)
    events = [event_out(e) for e in db.scalars(
        select(ExperimentEvent).where(ExperimentEvent.experiment_id == exp_id).order_by(ExperimentEvent.id)
    )]
    headers = {"Vary": "Accept"}
    if chosen == "application/json":
        return Response(
            json.dumps({"experiment": body, "events": events}, ensure_ascii=False),
            media_type="application/json", headers=headers,
        )
    return Response(render_markdown(body, events), media_type="text/markdown; charset=utf-8", headers=headers)
