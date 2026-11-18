"""Lab 02 – Prompt Injection. BẢN KHUNG (stub) của TV1: TV4 thay `run` bằng sandbox + Ollama (Colab).

Mỗi experiment chạy cả hai nhánh trước/sau phòng thủ (`defenses` mặc định [false, true], D-07). Tổng số lượt
gọi = len(case_codes) * len(defenses) * repeat ≤ settings.max_pi_calls (FR-PI-09).
Service không nhận URL nào: địa chỉ Ollama chỉ lấy từ cấu hình (D-18).
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
    is_int_in,
    reject_unknown,
    require_object,
)


class PromptInjectionService:
    code = "prompt_injection"

    def validate(self, payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
        payload = require_object(payload)
        errors = reject_unknown(payload, {"case_codes", "defenses", "repeat"})

        cases = payload.get("case_codes")
        cases_ok = isinstance(cases, list) and bool(cases) and all(is_code(c) for c in cases) and len(set(cases)) == len(cases)
        if not cases_ok:
            errors.append({"field": "case_codes", "message": "Cần danh sách mã ca không rỗng, không trùng (vd PI-01)."})

        defenses = payload.get("defenses", [False, True])
        defenses_ok = (
            isinstance(defenses, list)
            and bool(defenses)
            and all(isinstance(d, bool) for d in defenses)
            and len(set(defenses)) == len(defenses)
        )
        if not defenses_ok:
            errors.append({"field": "defenses", "message": "Cần danh sách boolean không trùng, ví dụ [false, true]."})

        repeat = payload.get("repeat", 1)
        repeat_ok = is_int_in(repeat, 1, 5)
        if not repeat_ok:
            errors.append({"field": "repeat", "message": "Phải là số nguyên 1..5."})

        if cases_ok and defenses_ok and repeat_ok:
            total = len(cases) * len(defenses) * repeat
            if total > settings.max_pi_calls:
                errors.append(
                    {"field": "repeat", "message": f"Tổng {total} lượt gọi mô hình vượt giới hạn {settings.max_pi_calls}."}
                )
        if errors:
            raise LabValidationError(errors)
        return {"case_codes": list(cases), "defenses": list(defenses), "repeat": repeat}

    def run(self, ctx: RunContext, payload: dict[str, Any]) -> LabResult:
        total = len(payload["case_codes"]) * len(payload["defenses"]) * payload["repeat"]
        ctx.emit("info", "stub", "Sandbox Prompt Injection chưa được tích hợp; không gọi mô hình.")
        ctx.check()
        return LabResult(
            result_kind="simulated",
            summary="Bản khung: chưa gọi mô hình nên chưa có kết quả tấn công nào.",
            components=[Component("sandbox", "simulated", "Bản khung của TV1, chưa gọi Ollama.")],
            metrics={"planned_calls": total, "judged": 0},
            limitations=["Bản khung của TV1: chưa gọi mô hình nên chưa có số liệu; không khẳng định phòng thủ hiệu quả."],
        )
