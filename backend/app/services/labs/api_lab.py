"""Lab 03 – API Security. BẢN KHUNG (stub) của TV1: TV4 thay `run` bằng request HTTP thật tới /sandbox/echo.

Chỉ nhận mã kịch bản và chế độ giới hạn tần suất; KHÔNG nhận URL/host nào (chống SSRF, FR-API-04).
`rate_limit_modes` là danh sách boolean (false = tắt, true = bật), mặc định [false, true] (API-01 mục 5.4).
"""
from __future__ import annotations

from typing import Any

from app.config import Settings
from app.services.base import (
    Component,
    LabResult,
    LabValidationError,
    RunContext,
    is_code,
    reject_unknown,
    require_object,
)


class ApiLabService:
    code = "api_lab"

    def validate(self, payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
        payload = require_object(payload)
        errors = reject_unknown(payload, {"scenario_code", "rate_limit_modes"})

        scenario = payload.get("scenario_code")
        if not is_code(scenario):
            errors.append({"field": "scenario_code", "message": "Cần mã kịch bản (vd burst-30), không phải URL/đường dẫn."})

        modes = payload.get("rate_limit_modes", [False, True])
        modes_ok = (
            isinstance(modes, list)
            and bool(modes)
            and all(isinstance(m, bool) for m in modes)
            and len(set(modes)) == len(modes)
        )
        if not modes_ok:
            errors.append({"field": "rate_limit_modes", "message": "Cần danh sách boolean không trùng, ví dụ [false, true]."})

        if errors:
            raise LabValidationError(errors)
        return {"scenario_code": scenario, "rate_limit_modes": list(modes)}

    def run(self, ctx: RunContext, payload: dict[str, Any]) -> LabResult:
        ctx.emit("info", "stub", "Kịch bản API Lab chưa được tích hợp; không gửi request nào.")
        ctx.check()
        return LabResult(
            result_kind="simulated",
            summary="Bản khung: chưa gửi request nên chưa có số liệu.",
            components=[Component("api_simulator", "simulated", "Bản khung của TV1, chưa gọi /sandbox/echo.")],
            metrics={"scenario_code": payload["scenario_code"], "requests_sent": 0},
            limitations=[
                "Bản khung của TV1: chưa mô phỏng lưu lượng, chưa có số liệu.",
                "Rate Limiting đơn lẻ không chống được mọi dạng DoS/DDoS.",
            ],
        )
