"""Lab 01 – Deepfake (TV2 tiền xử lý + TV3 detector).

Đầu vào: `asset_id` (media_assets, dạng `med_...`) + `frame_limit`. Không nhận đường dẫn/URL.

Ba trạng thái chạy trung thực theo SPEC 6, 9.3 và FR-DF-04/05/06:
1. Chưa có thư mục khung hình của TV2 (`storage/experiments/<id>/frames/`): giữ bản khung `simulated`
   (`detector: not_integrated`), bảo đảm hợp đồng cũ của TV1 không bị vỡ.
2. Đã có khung hình của TV2 nhưng chưa cấu hình mô hình detector (`DEEPFAKE_MODEL_PATH` rỗng):
   `preprocess` (`real`) + `detector` (`planned`) → `result_kind = "planned"`, `detector: not_integrated`,
   KHÔNG có `suspicion_score` hay `verdict` (FR-DF-04).
3. Đã có khung hình của TV2 và có mô hình detector hợp lệ (ONNX + kiểm tra SHA-256):
   `preprocess` (`real`) + `detector` (`real`) → `result_kind = "real"`, trả `suspicion_score`,
   `threshold`, `verdict ∈ {suspected, not_detected, insufficient_data}`, không dùng khóa cấm G4 (FR-DF-05).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.base import (
    Component,
    LabResult,
    LabRunError,
    LabValidationError,
    RunContext,
    is_int_in,
    reject_unknown,
    require_object,
)
from app.services.deepfake_detector import (
    AUDIO_LIMITATION,
    FIXED_WARNING,
    NOT_INTEGRATED_LIMITATION,
    FaceDetector,
    FrameScorer,
    ModelLoadError,
    analyze_experiment_dir,
    get_cached_scorer,
    list_frame_files,
    read_preprocess_metadata,
)

_ASSET_ID_RE = re.compile(r"^med_[A-Za-z0-9_-]{1,60}$")


class DeepfakeService:
    code = "deepfake"

    def __init__(
        self,
        *,
        scorer: FrameScorer | None = None,
        face_detector: FaceDetector | None = None,
    ) -> None:
        # Cho phép truyền scorer/face_detector giả khi unit test; chạy thật nạp từ settings.deepfake_model_file.
        self._injected_scorer = scorer
        self._injected_face_detector = face_detector

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

    def _resolve_scorer(self, settings: Settings) -> FrameScorer | None:
        if self._injected_scorer is not None:
            return self._injected_scorer
        model_file = settings.deepfake_model_file
        if model_file is None:
            return None
        return get_cached_scorer(
            model_file,
            model_name=settings.deepfake_model_name,
            expected_sha256=settings.deepfake_model_sha256,
            input_size=settings.deepfake_input_size,
        )

    def _experiment_dir(self, ctx: RunContext) -> Path:
        root = ctx.storage_dir.resolve()
        target = (root / "experiments" / ctx.experiment_id).resolve()
        if not target.is_relative_to(root):
            raise LabRunError("INTERNAL_ERROR", "Đường dẫn thư mục experiment không hợp lệ.")
        return target

    def run(self, ctx: RunContext, payload: dict[str, Any]) -> LabResult:
        ctx.check()
        exp_dir = self._experiment_dir(ctx)
        frames_dir = exp_dir / "frames"

        # Chế độ 1: TV2 chưa sinh khung hình trong storage/experiments/<id>/frames/ → bản khung an toàn
        if not frames_dir.is_dir():
            ctx.emit("info", "stub", "Chưa có khung hình tiền xử lý từ TV2; trả kết quả khung.")
            ctx.check()
            return LabResult(
                result_kind="simulated",
                summary="Bản khung: chưa phân tích video.",
                components=[
                    Component("pipeline", "simulated", "Bản khung: chưa có khung hình tiền xử lý để chạy detector."),
                ],
                metrics={
                    "asset_id": payload["asset_id"],
                    "frames_planned": payload["frame_limit"],
                    "detector": "not_integrated",
                },
                limitations=[
                    "Bản khung: chưa có khung hình tiền xử lý, không có tín hiệu thật.",
                    NOT_INTEGRATED_LIMITATION,
                    AUDIO_LIMITATION,
                    FIXED_WARNING,
                ],
            )

        frame_paths = list_frame_files(exp_dir, payload["frame_limit"])
        meta = read_preprocess_metadata(exp_dir)
        artifacts = [p.relative_to(ctx.storage_dir.resolve()).as_posix() for p in frame_paths]
        ctx.emit(
            "info",
            "frame_extracted",
            f"Đã đọc {len(frame_paths)} khung hình từ thư mục tiền xử lý.",
            {"frame_count": len(frame_paths), "has_metadata": bool(meta)},
        )

        # Kiểm tra xem có detector được cấu hình / phê duyệt hay không
        try:
            scorer = self._resolve_scorer(ctx.settings)
        except ModelLoadError as exc:
            ctx.emit("error", "detector_unavailable", "Không nạp được tệp trọng số mô hình Deepfake.")
            raise LabRunError("DEP_UNAVAILABLE", str(exc)) from exc

        # Chế độ 2: Đã có tiền xử lý thật (TV2), nhưng chưa tích hợp detector (FR-DF-04, MỞ-6)
        if scorer is None:
            ctx.check()
            ctx.emit("info", "detector_skipped", "Chưa cấu hình mô hình detector; bỏ qua bước chấm điểm.")
            metrics: dict[str, Any] = {
                "asset_id": payload["asset_id"],
                "frames_extracted": len(frame_paths),
                "detector": "not_integrated",
            }
            if meta:
                for key in ("duration_s", "fps", "width", "height", "codec", "has_audio"):
                    if key in meta:
                        metrics[key] = meta[key]
            return LabResult(
                result_kind="planned",
                summary=f"Đã tiền xử lý {len(frame_paths)} khung hình; chưa tích hợp mô hình phát hiện Deepfake.",
                components=[
                    Component("preprocess", "real", f"Đã nạp {len(frame_paths)} khung hình từ tiền xử lý."),
                    Component("detector", "planned", "Chưa tích hợp mô hình phát hiện được phê duyệt (FR-DF-04)."),
                ],
                metrics=metrics,
                artifacts=artifacts,
                limitations=[
                    NOT_INTEGRATED_LIMITATION,
                    AUDIO_LIMITATION,
                    FIXED_WARNING,
                ],
            )

        # Chế độ 3: Tiền xử lý thật (TV2) + Detector thật (TV3, FR-DF-05)
        try:
            outcome = analyze_experiment_dir(
                exp_dir,
                ctx,
                frame_limit=payload["frame_limit"],
                scorer=scorer,
                face_detector=self._injected_face_detector,
            )
        except ModelLoadError as exc:
            ctx.emit("error", "detector_unavailable", "Lỗi bộ phát hiện khuôn mặt/mô hình Deepfake.")
            raise LabRunError("DEP_UNAVAILABLE", str(exc)) from exc

        ctx.emit(
            "info",
            "detector_completed",
            f"Hoàn tất suy luận Deepfake: verdict={outcome.verdict}, faces={outcome.faces_detected}/{outcome.frames_analyzed}.",
            {
                "verdict": outcome.verdict,
                "frames_analyzed": outcome.frames_analyzed,
                "faces_detected": outcome.faces_detected,
                "quality": outcome.quality,
                "inference_ms": outcome.inference_ms,
            },
        )

        if outcome.verdict == "suspected" and outcome.suspicion_score is not None:
            verdict_text = (
                f"Phát hiện dấu hiệu nghi ngờ giả mạo (điểm {outcome.suspicion_score:.3f} >= ngưỡng {outcome.threshold:.2f})."
            )
        elif outcome.verdict == "not_detected" and outcome.suspicion_score is not None:
            verdict_text = (
                f"Chưa ghi nhận dấu hiệu bất thường vượt ngưỡng (điểm {outcome.suspicion_score:.3f} < ngưỡng {outcome.threshold:.2f})."
            )
        else:
            verdict_text = "Không đủ khung hình có khuôn mặt rõ để đánh giá (insufficient_data)."

        metrics_real: dict[str, Any] = {
            "asset_id": payload["asset_id"],
            "detector": outcome.model_name,
            "model_sha256": outcome.model_sha256[:12],
            "frames_analyzed": outcome.frames_analyzed,
            "faces_detected": outcome.faces_detected,
            "quality": outcome.quality,
            "threshold": outcome.threshold,
            "suspicion_score": outcome.suspicion_score,
            "verdict": outcome.verdict,
            "inference_ms": outcome.inference_ms,
        }
        if meta:
            for key in ("duration_s", "fps", "width", "height", "codec", "has_audio"):
                if key in meta:
                    metrics_real[key] = meta[key]

        limitations = [
            FIXED_WARNING,
            AUDIO_LIMITATION,
            "Điểm nghi ngờ (suspicion_score) phản ánh tín hiệu mô hình trên các khung hình cắt mặt, không phải xác suất tuyệt đối.",
            "Độ tin cậy giảm khi video mờ, nén mạnh, thiếu sáng, góc nghiêng hoặc dùng kỹ thuật tạo giả mới.",
        ]
        if outcome.verdict == "insufficient_data":
            limitations.insert(
                0,
                f"Chỉ phát hiện khuôn mặt ở {outcome.faces_detected}/{outcome.frames_analyzed} khung hình "
                f"(tối thiểu cần {ctx.settings.deepfake_min_faces}).",
            )

        return LabResult(
            result_kind="real",
            summary=verdict_text,
            components=[
                Component("preprocess", "real", f"Đã nạp {outcome.frames_analyzed} khung hình từ tiền xử lý."),
                Component(
                    "detector",
                    "real",
                    f"Mô hình {outcome.model_name} (sha256:{outcome.model_sha256[:12]}, threshold={outcome.threshold}).",
                ),
            ],
            metrics=metrics_real,
            artifacts=list(outcome.artifacts),
            limitations=limitations,
        )
