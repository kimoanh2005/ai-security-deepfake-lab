"""Lab 01 – Deepfake. BẢN KHUNG (stub) của TV1: TV2/TV3 thay phần `run` bằng pipeline thật.

Đầu vào: asset_id (media_assets, dạng med_...) + frame_limit. Không nhận đường dẫn/URL.
Khi chưa có detector được phê duyệt: không có điểm số hay nhãn giả/thật (FR-DF-04) và luôn có cảnh báo
cố định (FR-DF-06).
"""
from __future__ import annotations

import re
from typing import Any

from app.config import Settings
from app.services.base import (
    Component,
    LabResult,
    LabValidationError,
    RunContext,
    is_int_in,
    reject_unknown,
    require_object,
)

_ASSET_ID_RE = re.compile(r"^med_[A-Za-z0-9_-]{1,60}$")


class DeepfakeService:
    code = "deepfake"

    def validate(self, payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
        payload = require_object(payload)
        errors = reject_unknown(payload, {"asset_id", "frame_limit"})
        asset_id = payload.get("asset_id")
        if not isinstance(asset_id, str) or _ASSET_ID_RE.fullmatch(asset_id) is None:
            errors.append({"field": "asset_id", "message": "Cần định danh media dạng med_..."})
        frame_limit = payload.get("frame_limit", settings.max_frames)
        if not is_int_in(frame_limit, 1, settings.max_frames):
            errors.append({"field": "frame_limit", "message": f"Phải là số nguyên 1..{settings.max_frames}."})
        if errors:
            raise LabValidationError(errors)
        return {"asset_id": asset_id, "frame_limit": frame_limit}

    def run(self, ctx: RunContext, payload: dict[str, Any]) -> LabResult:
        ctx.emit("info", "stub", "Pipeline Deepfake chưa được tích hợp; trả kết quả khung.")
        ctx.check()
        return LabResult(
            result_kind="simulated",
            summary="Bản khung: chưa phân tích video.",
            components=[
                Component("pipeline", "simulated", "Bản khung của TV1, chưa chạy tiền xử lý thật."),
            ],
            metrics={
                "asset_id": payload["asset_id"],
                "frames_planned": payload["frame_limit"],
                "detector": "not_integrated",
            },
            limitations=[
                "Bản khung của TV1: chưa phân tích video, không có tín hiệu thật.",
                "Chưa có phát hiện tự động (detector: not_integrated); không có điểm số hay nhãn giả/thật.",
                "Không phải kết luận chắc chắn về danh tính hoặc sự thật của video.",
            ],
        )
