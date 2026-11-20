"""Hợp đồng giữa orchestrator (TV1) và các Lab service (TV2–TV4).

Quy tắc cho người viết service:
- Service KHÔNG chạm CSDL và không import `app.api`, `app.models`, `app.jobs` (SPEC 5.4). Dữ liệu cần lưu
  vào bảng nghiệp vụ được trả trong `LabResult.records`; orchestrator ghi trong một giao dịch.
- `validate` chỉ nhận khóa đã biết (dùng `reject_unknown`), không nhận URL/đường dẫn tự do.
- `run` gọi `ctx.emit(...)` cho mốc quan trọng/cảnh báo và `ctx.check()` định kỳ để dừng sớm khi hết giờ.
- `limitations` bắt buộc không rỗng; `result_kind` phải bằng loại YẾU NHẤT trong `components` (D-10) và
  không mạnh hơn `labs.status`. Không đủ điều kiện chạy thật thì trả nhãn thấp hơn, KHÔNG bịa số liệu.

LabResult và Component là dataclass (không phải pydantic) để service sai nhãn vẫn dựng được đối tượng và
`result_guard` là nơi duy nhất từ chối, ghi rõ lý do.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from app.config import Settings

ResultKind = Literal["real", "simulated", "planned"]
RESULT_KINDS: tuple[str, ...] = ("real", "simulated", "planned")  # khớp app.models.database.RESULT_KINDS (có test)
# Số lớn hơn = yếu hơn. Kết quả tổng hợp lấy loại yếu nhất (D-10).
KIND_ORDER: dict[str, int] = {"real": 0, "simulated": 1, "planned": 2}

# Cột service được phép đặt trong `records` cho từng bảng nghiệp vụ. Orchestrator tự thêm id, experiment_id,
# created_at. `case_code` thay cho `case_id`: orchestrator tra mã ca trong prompt_cases rồi đổi sang case_id.
RECORD_COLUMNS: dict[str, frozenset[str]] = {
    "prompt_runs": frozenset(
        {"case_code", "attempt", "defense", "response", "latency_ms", "model_name", "task_violated", "judged_by"}
    ),
}

CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


def weakest_kind(kinds: list[str]) -> str:
    return max(kinds, key=lambda k: KIND_ORDER[k])


@dataclass
class Component:
    name: str
    kind: str  # real | simulated | planned
    note: str | None = None


@dataclass
class LabResult:
    result_kind: str
    summary: str
    components: list[Component]
    limitations: list[str]
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)  # đường dẫn tương đối trong storage/
    records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


class LabValidationError(Exception):
    """Đầu vào không hợp lệ; `errors` là danh sách {field, message}. Orchestrator trả 422 và KHÔNG tạo experiment."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__("; ".join(f"{e['field']}: {e['message']}" for e in errors))
        self.errors = errors


class JobCancelled(Exception):
    """Ném bởi ctx.check() khi hết thời gian hoặc bị hủy."""


# Mã lỗi service được phép báo qua LabRunError (FR-PI-10). Mã khác bị orchestrator đổi thành INTERNAL_ERROR.
LAB_RUN_ERROR_CODES: tuple[str, ...] = ("DEP_UNAVAILABLE", "JOB_TIMEOUT")


class LabRunError(Exception):
    """Service báo job thất bại có chủ đích, kèm mã lỗi và dữ liệu đã có trước khi lỗi (FR-PI-10).

    Dùng khi phụ thuộc ngoài mất kết nối giữa chừng (Ollama trên Colab ngắt phiên): experiment thành `failed` với
    `error.code = code`, còn `partial_records` (cùng dạng `LabResult.records`) vẫn được lưu để không mất các lượt
    đã chạy thật. Không có kết quả cấp experiment: không `lab_results`, không `result_kind`.

    - `code` ∈ LAB_RUN_ERROR_CODES; mã khác → `INTERNAL_ERROR`.
    - `message` chỉ ghi log máy chủ, KHÔNG trả cho client (thông điệp ngoại lệ mạng có thể chứa địa chỉ đường hầm).
    - `partial_records` đi qua cùng kiểm tra `records` của guard; sai → bị bỏ (experiment vẫn `failed`).
    """

    def __init__(
        self,
        code: str,
        message: str = "",
        partial_records: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.partial_records = partial_records or {}


class RunContext:
    def __init__(
        self,
        settings: Settings,
        experiment_id: str,
        storage_dir: Path,
        timeout_seconds: float,
    ) -> None:
        self.settings = settings
        self.experiment_id = experiment_id
        self.storage_dir = storage_dir
        self.events: list[dict[str, Any]] = []
        self._deadline = time.monotonic() + timeout_seconds
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    def emit(self, level: str, event_type: str, message: str, data: dict[str, Any] | None = None) -> None:
        """Ghi một sự kiện vào experiment_events (orchestrator lưu). Không đưa mật khẩu/token/nội dung đầy đủ vào."""
        if level not in ("info", "warn", "error"):
            level = "info"
        with self._lock:
            if len(self.events) < self.settings.max_events_per_experiment:
                self.events.append(
                    {"level": level, "event_type": event_type[:64], "message": message[:500], "data": data}
                )

    def cancel(self) -> None:
        self._cancel.set()

    def check(self) -> None:
        if self._cancel.is_set() or time.monotonic() > self._deadline:
            raise JobCancelled("Hết thời gian hoặc bị hủy")


class LabService(Protocol):
    code: str

    def validate(self, payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
        """Trả input đã chuẩn hóa (có giá trị mặc định) hoặc ném LabValidationError."""

    def run(self, ctx: RunContext, payload: dict[str, Any]) -> LabResult:
        """`payload` là giá trị `validate` đã trả về."""


# ---- Tiện ích cho validate() ----

def require_object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise LabValidationError([{"field": "", "message": "Thân yêu cầu phải là một đối tượng JSON."}])
    return payload


def reject_unknown(payload: dict[str, Any], allowed: set[str]) -> list[dict[str, str]]:
    return [{"field": str(k), "message": "Trường không được hỗ trợ."} for k in payload if k not in allowed]


def is_code(value: Any) -> bool:
    """Mã ngắn kiểu PI-01, burst-30: chỉ chữ/số/gạch, không chứa '/', '://' hay khoảng trắng."""
    return isinstance(value, str) and CODE_RE.fullmatch(value) is not None


def is_int_in(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
