"""Lab 02 – Prompt Injection. BẢN KHUNG (stub) của TV1: TV4 thay `run` bằng sandbox + Ollama (Colab).

Mỗi experiment chạy cả hai nhánh trước/sau phòng thủ (`defenses` mặc định [false, true], D-07). Tổng số lượt
gọi = len(case_codes) * len(defenses) * repeat ≤ settings.max_pi_calls (FR-PI-09).
Service không nhận URL nào: địa chỉ Ollama chỉ lấy từ cấu hình (D-18).

Tích hợp TV4: bộ ca (`pi_cases`) và lõi phát hiện/chặn/chấm (`pi_engine`) lấy nguyên từ nhánh Diep, chỉ bọc lại cho
đúng hợp đồng LabService (không chạm CSDL, kết quả qua `records`, lỗi Colab qua `LabRunError`).
"""
from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings
from app.core.ollama import make_generate
from app.services.base import (
    Component,
    JobCancelled,
    LabResult,
    LabRunError,
    LabValidationError,
    RunContext,
    is_code,
    is_int_in,
    reject_unknown,
    require_object,
)
from app.services.labs import pi_engine
from app.services.labs.pi_cases import CASE_CODES, CASES_BY_CODE, document_for


class PromptInjectionService:
    code = "prompt_injection"
    # Chỉ để test tiêm httpx.MockTransport; chạy thật để None (httpx tự dùng mạng, vẫn qua allowlist D-18).
    transport: httpx.BaseTransport | None = None

    def validate(self, payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
        payload = require_object(payload)
        errors = reject_unknown(payload, {"case_codes", "defenses", "repeat"})

        cases = payload.get("case_codes")
        cases_ok = isinstance(cases, list) and bool(cases) and all(is_code(c) for c in cases) and len(set(cases)) == len(cases)
        if not cases_ok:
            errors.append({"field": "case_codes", "message": "Cần danh sách mã ca không rỗng, không trùng (vd PI-01)."})
        elif any(c not in CASES_BY_CODE for c in cases):
            errors.append({"field": "case_codes", "message": f"Mã ca không tồn tại. Hợp lệ: {CASE_CODES[0]}..{CASE_CODES[-1]}."})

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
        generate = make_generate(ctx.settings, transport=self.transport)
        model = (ctx.settings.ollama_model or "").strip() or "security-bot"
        rows: list[dict[str, Any]] = []
        ctx.emit("info", "pi_start", f"Bắt đầu {total} lượt gọi mô hình.", {"planned_calls": total})
        try:
            for code in payload["case_codes"]:
                case = CASES_BY_CODE[code]
                document = document_for(case)
                for attempt in range(1, payload["repeat"] + 1):
                    for defense in payload["defenses"]:
                        ctx.check()
                        result = pi_engine.execute(case["prompt"], document, defense, generate)
                        if not result["success"]:
                            ctx.emit("warn", "pi_upstream_error", "Mô hình không phản hồi hợp lệ; dừng experiment.",
                                     {"case_code": code, "completed": len(rows)})
                            raise LabRunError("DEP_UNAVAILABLE", "Ollama không phản hồi hợp lệ.", {"prompt_runs": rows})
                        violated = pi_engine.judge(case, result)
                        rows.append({
                            "case_code": code, "attempt": attempt, "defense": defense, "response": result["response"],
                            "latency_ms": result["duration_ms"], "model_name": model,
                            "task_violated": None if violated is None else int(violated),
                            "judged_by": None if violated is None else "rule",
                        })
        except JobCancelled as exc:
            raise LabRunError("JOB_TIMEOUT", str(exc), {"prompt_runs": rows}) from exc
        finally:
            close = getattr(generate, "close", None)
            if close:
                close()

        categories = {c: CASES_BY_CODE[c]["category"] for c in payload["case_codes"]}
        metrics: dict[str, Any] = {"planned_calls": total, "completed_calls": len(rows), "model": model,
                                   "judged": sum(r["judged_by"] == "rule" for r in rows),
                                   "needs_review": sum(r["judged_by"] is None for r in rows)}
        for flag, name in ((False, "baseline"), (True, "defense")):
            if flag not in payload["defenses"]:
                continue
            attacks = [r for r in rows if r["defense"] is flag and categories[r["case_code"]] in ("attack", "indirect")]
            judged = [r for r in attacks if r["task_violated"] is not None]
            # ASR chỉ công bố khi MỌI lượt tấn công của nhánh đã có kết luận (TV4: thiếu dữ liệu thì không tính).
            complete = bool(attacks) and len(judged) == len(attacks)
            metrics[name] = {"attack_runs": len(attacks), "judged": len(judged),
                             "asr": sum(r["task_violated"] == 1 for r in attacks) / len(attacks) if complete else None}
        ctx.emit("info", "pi_done", "Hoàn tất các lượt gọi mô hình.", {"completed_calls": len(rows)})
        return LabResult(
            result_kind="simulated",
            summary=f"Đã chạy {len(rows)} lượt trên mô hình {model}; {metrics['needs_review']} lượt cần người chấm.",
            components=[
                Component("sandbox", "simulated", "Tài liệu và bí mật FLAG là dữ liệu giả lập."),
                Component("ollama_model", "real", f"Phản hồi do mô hình {model} thật sinh qua Ollama trên Colab."),
            ],
            metrics=metrics,
            limitations=[
                "Phát hiện tấn công bằng quy tắc từ khóa (heuristic) nên có thể bỏ sót hoặc chặn nhầm; không phải bằng chứng tấn công thành công.",
                "Chỉ chấm tự động khi có rò rỉ rõ (vi phạm) hoặc nhánh phòng thủ chặn đầu vào (không vi phạm); các lượt còn lại cần người chấm.",
                "ASR chỉ có khi mọi lượt tấn công của nhánh đã được chấm; mô hình nhỏ và bộ ca hữu hạn nên không khái quát hóa.",
            ],
            records={"prompt_runs": rows},
        )
