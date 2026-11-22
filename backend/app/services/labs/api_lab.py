"""Lab 03 – API Security: tích hợp `measure_api` của TV4 (nhánh Diep, services/api_lab_service.py).

Giữ nguyên phép đo của TV4: mỗi nhánh gửi N request thật tới /sandbox/echo, đếm 200/429/lỗi và p50/p95
(`percentile` nguyên văn). Khác ở lớp giao tiếp theo hợp đồng TV1: chạy đồng bộ trong worker, đích và token lấy từ
`ctx.settings`, nhánh chọn bằng header `X-Lab-Rate-Limit`, mỗi lần chạy có bộ đếm riêng (`X-Lab-Run`).

Chỉ nhận mã kịch bản và chế độ giới hạn tần suất; KHÔNG nhận URL/host nào (chống SSRF, FR-API-04).
`rate_limit_modes` là danh sách boolean (false = tắt, true = bật), mặc định [false, true] (API-01 mục 5.4).
"""
from __future__ import annotations

import math
import re
from time import perf_counter
from typing import Any

import httpx

from app.config import Settings
from app.services.base import (
    Component,
    LabResult,
    LabRunError,
    LabValidationError,
    RunContext,
    is_code,
    reject_unknown,
    require_object,
)


SCENARIO_RE = re.compile(r"^burst-(\d{1,6})$")  # burst-N: N request liên tiếp mỗi nhánh
SANDBOX_TIMEOUT_SECONDS = 10.0


def percentile(values: list[float], quantile: float) -> float | None:
    """Nguyên văn từ TV4 (lab_service.percentile): nội suy tuyến tính; rỗng → None."""
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * quantile
    lo, hi = math.floor(position), math.ceil(position)
    return round(values[lo] + (values[hi] - values[lo]) * (position - lo), 3)


class ApiLabService:
    code = "api_lab"
    # Chỉ để test tiêm httpx.MockTransport; chạy thật để None.
    transport: httpx.BaseTransport | None = None

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

        if is_code(scenario) and modes_ok:
            match = SCENARIO_RE.fullmatch(scenario)
            if match is None or int(match.group(1)) < 1:
                errors.append({"field": "scenario_code", "message": "Kịch bản hỗ trợ: burst-N (N >= 1), vd burst-30."})
            elif int(match.group(1)) * len(modes) > settings.max_api_lab_requests:
                errors.append({"field": "scenario_code",
                               "message": f"Tổng số request vượt giới hạn {settings.max_api_lab_requests}."})

        if errors:
            raise LabValidationError(errors)
        return {"scenario_code": scenario, "rate_limit_modes": list(modes)}

    def run(self, ctx: RunContext, payload: dict[str, Any]) -> LabResult:
        count = int(SCENARIO_RE.fullmatch(payload["scenario_code"]).group(1))  # type: ignore[union-attr]
        settings = ctx.settings
        url = settings.sandbox_base_url.rstrip("/") + "/sandbox/echo"  # đích cố định, đã kiểm loopback ở Settings
        base_headers = {"X-Lab-Token": settings.sandbox_token, "X-Lab-Run": ctx.experiment_id}
        rows: list[dict[str, Any]] = []
        ctx.emit("info", "api_start", f"Gửi {count} request mỗi nhánh tới sandbox.", {"requests_per_branch": count})
        with httpx.Client(timeout=SANDBOX_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False,
                          transport=self.transport) as client:
            for mode in payload["rate_limit_modes"]:
                headers = {**base_headers, "X-Lab-Rate-Limit": "on" if mode else "off"}
                for index in range(1, count + 1):
                    ctx.check()
                    started = perf_counter()
                    try:
                        resp = client.post(url, json={"message": "lab probe"}, headers=headers)
                    except httpx.HTTPError as exc:
                        raise LabRunError("DEP_UNAVAILABLE", "Không kết nối được sandbox.") from exc
                    if resp.status_code not in (200, 429):
                        # 401/5xx: sandbox hoặc token cấu hình sai; số liệu từ nó không đáng tin → dừng.
                        raise LabRunError("DEP_UNAVAILABLE", f"Sandbox trả HTTP {resp.status_code}.")
                    rows.append({"mode": mode, "index": index, "status": resp.status_code,
                                 "duration_ms": round((perf_counter() - started) * 1000, 3)})
                    if resp.status_code == 429:
                        ctx.emit("warn", "rate_limited", "Sandbox trả 429.", {"rate_limit": "on" if mode else "off", "index": index})

        metrics: dict[str, Any] = {"scenario_code": payload["scenario_code"], "requests_sent": len(rows)}
        for mode in payload["rate_limit_modes"]:
            selected = [r for r in rows if r["mode"] is mode]
            durations = [r["duration_ms"] for r in selected]
            metrics["rate_limit_on" if mode else "rate_limit_off"] = {
                "sent": len(selected),
                "http_200": sum(r["status"] == 200 for r in selected),
                "http_429": sum(r["status"] == 429 for r in selected),
                "p50_ms": percentile(durations, 0.5),
                "p95_ms": percentile(durations, 0.95),
            }
        ctx.emit("info", "api_done", "Hoàn tất các request.", {"requests_sent": len(rows)})
        return LabResult(
            result_kind="simulated",
            summary=f"Đã gửi {len(rows)} request thật tới sandbox cục bộ ({payload['scenario_code']}).",
            components=[
                Component("sandbox_target", "simulated", "Đối tượng bị thử là /sandbox/echo của ứng dụng demo nội bộ."),
                Component("http_traffic", "real", "Request HTTP thật từ worker tới sandbox qua httpx."),
            ],
            metrics=metrics,
            limitations=[
                "Rate Limiting đơn lẻ không chống được mọi dạng DoS/DDoS.",
                "Đo trên loopback, độ trễ không phản ánh mạng thật; chỉ so sánh tương đối giữa hai nhánh.",
                "Chỉ một bộ đếm theo lần chạy trên một tiến trình; không mô phỏng nhiều máy khách hay IP.",
            ],
        )
