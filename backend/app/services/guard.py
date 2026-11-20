"""result_guard G1–G7 (Cổng 3, đầu ra) theo SPEC mục 6.1.

Mọi LabResult phải qua guard trước khi lưu `done`. Guard KHÔNG ném ngoại lệ với đầu vào lạ: service sai thì
kết quả bị từ chối kèm lý do, không làm sập worker.

| Mã | Điều kiện |
|----|-----------|
| G1 | result_kind ∈ {real, simulated, planned} |
| G2 | result_kind không mạnh hơn trần `labs.status` |
| G3 | limitations là danh sách chuỗi không rỗng |
| G4 | không có khóa probability/accuracy/confidence_pct ở mức metrics, trừ khi metrics.calibrated is True |
| G5 | mỗi components[i].kind hợp lệ và result_kind = mức yếu nhất của các thành phần |
| G6 | artifacts là đường dẫn tương đối, không thoát khỏi storage/ |
| G7 | kết quả tuần tự hóa được thành JSON và ≤ max_result_bytes |

Ngoài ra có hai kiểm tra bổ sung: `shape` (đúng kiểu LabResult) và `records` (chỉ bảng/cột được phép, không tự
gán `task_violated` — FR-PI-05).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from app.config import Settings
from app.services.base import KIND_ORDER, RECORD_COLUMNS, RESULT_KINDS, LabResult, weakest_kind

# Số liệu dạng "xác suất" chỉ được ở cấp cao nhất của metrics khi đã hiệu chuẩn (metrics.calibrated is True).
FORBIDDEN_METRIC_KEYS = frozenset({"probability", "accuracy", "confidence_pct"})


@dataclass
class GuardReport:
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(c["ok"] for c in self.checks.values())

    @property
    def failed(self) -> dict[str, str]:
        return {name: c["detail"] for name, c in self.checks.items() if not c["ok"]}

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks[name] = {"ok": ok, "detail": detail}

    def as_dict(self) -> dict[str, Any]:
        """Dạng lưu vào lab_results.guard_details."""
        return {"passed": self.passed, "checks": self.checks}


def _unsafe_artifact(path: Any) -> str | None:
    """Trả lý do nếu `path` không phải đường dẫn tương đối an toàn; None nếu ổn."""
    if not isinstance(path, str) or not path.strip():
        return "đường dẫn rỗng hoặc không phải chuỗi"
    if "\\" in path or ":" in path or "\x00" in path:
        return "không được chứa '\\', ':' hoặc ký tự NUL"
    p = PurePosixPath(path)
    if p.is_absolute():
        return "phải là đường dẫn tương đối"
    if ".." in p.parts:
        return "không được chứa '..'"
    return None


def _is_flag(value: Any) -> bool:
    return isinstance(value, (bool, int)) and value in (0, 1)


def _is_int(value: Any, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _check_prompt_run_types(row: dict[str, Any]) -> str:
    """Kiểu của một dòng prompt_runs. SQLite không ép kiểu cột, nên chặn ở đây thay vì để dữ liệu sai lọt vào."""
    if not isinstance(row.get("case_code"), str) or not row["case_code"]:
        return "prompt_runs: thiếu case_code (chuỗi không rỗng)."
    if "defense" not in row or not _is_flag(row["defense"]):
        return "prompt_runs: defense bắt buộc và phải là boolean (hoặc 0/1)."
    if "attempt" in row and not _is_int(row["attempt"], 1):
        return "prompt_runs: attempt phải là số nguyên >= 1."
    if row.get("latency_ms") is not None and not _is_int(row["latency_ms"], 0):
        return "prompt_runs: latency_ms phải là số nguyên >= 0 hoặc null."
    for key in ("response", "model_name"):
        if row.get(key) is not None and not isinstance(row[key], str):
            return f"prompt_runs: {key} phải là chuỗi hoặc null."
    return ""


def check_records(records: Any) -> str:
    """Trả chuỗi lỗi (rỗng nếu ổn). Dùng chung cho kết quả đầy đủ và `LabRunError.partial_records`."""
    if not isinstance(records, dict):
        return "records phải là object {tên_bảng: [dòng, ...]}."
    for table, rows in records.items():
        allowed = RECORD_COLUMNS.get(table)
        if allowed is None:
            return f"bảng {table!r} không được phép ghi."
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            return f"records[{table!r}] phải là danh sách object."
        for row in rows:
            extra = set(row) - allowed
            if extra:
                return f"bản ghi của {table!r} có cột ngoài danh sách cho phép: {sorted(map(str, extra))}."
            if table == "prompt_runs":
                bad_type = _check_prompt_run_types(row)
                if bad_type:
                    return bad_type
                # Không tự gán thành công (FR-PI-05): service chỉ được đặt kết quả khi chấm bằng quy tắc.
                violated, judged_by = row.get("task_violated"), row.get("judged_by")
                if violated is None and judged_by is not None:
                    return "prompt_runs: judged_by chỉ hợp lệ khi task_violated đã được chấm."
                if violated is not None and (violated not in (0, 1, True, False) or judged_by != "rule"):
                    return "prompt_runs: task_violated chỉ được đặt cùng judged_by='rule'; người chấm là 'human' qua API."
    return ""


def run_guard(result: Any, lab_ceiling: str, settings: Settings) -> GuardReport:
    r = GuardReport()

    # shape: đúng kiểu để các kiểm tra sau chạy được
    shape_ok = (
        isinstance(result, LabResult)
        and isinstance(result.metrics, dict)
        and isinstance(result.summary, str)
        and isinstance(result.components, list)
        and isinstance(result.artifacts, list)
    )
    r.add("shape", shape_ok, "" if shape_ok else "kết quả không phải LabResult hợp lệ (kiểu các trường sai).")
    if not shape_ok:
        return r

    # G1
    g1 = result.result_kind in RESULT_KINDS
    r.add("G1_kind_valid", g1, "" if g1 else f"result_kind={result.result_kind!r} không hợp lệ.")

    # G2: không mạnh hơn trần của Lab
    if g1 and lab_ceiling in KIND_ORDER:
        g2 = KIND_ORDER[result.result_kind] >= KIND_ORDER[lab_ceiling]
        r.add("G2_within_lab_ceiling", g2, "" if g2 else f"Lab {lab_ceiling} không được trả {result.result_kind}.")
    else:
        r.add("G2_within_lab_ceiling", False, "không xác định được result_kind hoặc trần của Lab.")

    # G3
    lim = result.limitations
    g3 = isinstance(lim, list) and len(lim) > 0 and all(isinstance(x, str) and x.strip() for x in lim)
    r.add("G3_limitations", g3, "" if g3 else "limitations phải là danh sách chuỗi không rỗng.")

    # G4
    bad = sorted(FORBIDDEN_METRIC_KEYS & {k for k in result.metrics if isinstance(k, str)})
    g4 = not bad or result.metrics.get("calibrated") is True
    r.add("G4_no_misleading_metrics", g4, "" if g4 else f"khóa bị cấm khi chưa hiệu chuẩn: {bad}")

    # G5
    comps = result.components
    kinds = [getattr(c, "kind", None) for c in comps]
    if not comps or any(k not in RESULT_KINDS for k in kinds) or any(not getattr(c, "name", "") for c in comps):
        r.add("G5_kind_is_weakest", False, "components rỗng, thiếu name hoặc có kind không hợp lệ.")
    elif not g1:
        r.add("G5_kind_is_weakest", False, "result_kind không hợp lệ nên không so với components được.")
    else:
        expected = weakest_kind(kinds)
        g5 = expected == result.result_kind
        r.add("G5_kind_is_weakest", g5, "" if g5 else f"cần {expected} (yếu nhất của components), nhận {result.result_kind}.")

    # G6
    reasons = [f"{p!r}: {why}" for p in result.artifacts if (why := _unsafe_artifact(p))]
    r.add("G6_artifacts_relative", not reasons, "; ".join(reasons[:3]))

    # G7
    g7, detail = True, ""
    try:
        payload = {
            "summary": result.summary,
            "components": [vars(c) if hasattr(c, "__dict__") else c for c in comps],
            "metrics": result.metrics,
            "artifacts": result.artifacts,
            "limitations": result.limitations,
            "records": result.records,
        }
        size = len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        if size > settings.max_result_bytes:
            g7, detail = False, f"kết quả {size} byte vượt giới hạn {settings.max_result_bytes}."
    except (TypeError, ValueError) as exc:
        g7, detail = False, f"không tuần tự hóa được thành JSON: {exc}"
    r.add("G7_serializable_and_size", g7, detail)

    rec = check_records(result.records)
    r.add("records", not rec, rec)
    return r
