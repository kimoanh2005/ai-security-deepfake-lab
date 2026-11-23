"""Kiểm thử toàn diện phần TV3: deepfake_detector, DeepfakeService (3 chế độ), guard G1–G7, đánh giá FPR/FNR/Precision."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.config import Settings
from app.services.base import JobCancelled, LabRunError, RunContext
from app.services.deepfake_detector import (
    ModelLoadError,
    VideoEvalRow,
    _logits_to_score,
    aggregate_frame_scores,
    analyze_experiment_dir,
    classify_score,
    evaluate_predictions,
    load_manifest,
    render_evaluation_markdown,
    sweep_thresholds,
    verify_model_sha256,
)
from app.services.guard import run_guard
from app.services.labs.deepfake import DeepfakeService


class _StubFaceDetector:
    """Bộ phát hiện mặt giả cho unit test: trả mặt cho các ảnh có độ sáng trung bình > 20."""

    def __init__(self, *, always_none: bool = False) -> None:
        self._always_none = always_none

    def extract_face(self, bgr: np.ndarray) -> np.ndarray | None:
        if self._always_none or float(np.mean(bgr)) <= 20.0:
            return None
        return bgr[10:90, 10:90]


class _StubScorer:
    def __init__(self, scores: list[float], name: str = "test-onnx-v1") -> None:
        self.name = name
        self.sha256 = "a" * 64
        self._scores = list(scores)
        self._idx = 0

    def score_face(self, face_bgr: np.ndarray) -> float:
        val = self._scores[self._idx % len(self._scores)]
        self._idx += 1
        return val


def _make_settings(**over) -> Settings:
    return Settings(_env_file=None, jwt_secret="pytest-secret-0123456789abcdef", **over)


def _create_frames(storage: Path, exp_id: str, count: int, *, bright: bool = True) -> Path:
    exp_dir = storage / "experiments" / exp_id
    frames_dir = exp_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    val = 180 if bright else 5
    img = np.full((120, 120, 3), val, dtype=np.uint8)
    for i in range(1, count + 1):
        cv2.imwrite(str(frames_dir / f"{i:06d}.jpg"), img)
    (exp_dir / "metadata.json").write_text(
        '{"duration_s": 5.0, "fps": 25.0, "width": 120, "height": 120, "codec": "h264", "frame_count": %d, "has_audio": true}'
        % count,
        encoding="utf-8",
    )
    return exp_dir


# ---- Quy tắc phụ thuộc SPEC 5.4 ----

@pytest.mark.parametrize(
    "rel_path",
    ["app/services/deepfake_detector.py", "app/services/labs/deepfake.py"],
)
def test_services_do_not_import_forbidden_layers(rel_path):
    base = Path(__file__).resolve().parents[1] / rel_path
    tree = ast.parse(base.read_text(encoding="utf-8"))
    forbidden = ("app.api", "app.models", "app.jobs")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(forbidden), alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(forbidden), node.module


# ---- Kiểm tra toàn vẹn SHA-256 (Cổng 2) ----

def test_verify_model_sha256_ok_and_mismatch(tmp_path: Path):
    f = tmp_path / "dummy.onnx"
    content = b"fake-onnx-weights-content"
    f.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()

    assert verify_model_sha256(f, expected) == expected
    assert verify_model_sha256(f, "") == expected

    with pytest.raises(ModelLoadError):
        verify_model_sha256(f, "0" * 64)
    with pytest.raises(ModelLoadError):
        verify_model_sha256(f, "not-a-hex")
    with pytest.raises(ModelLoadError):
        verify_model_sha256(tmp_path / "missing.onnx", expected)


# ---- Suy luận & tổng hợp điểm ----

def test_logits_to_score_and_aggregation():
    assert 0.0 <= _logits_to_score(np.array([0.8])) <= 1.0
    assert _logits_to_score(np.array([0.8])) == pytest.approx(0.8)
    assert _logits_to_score(np.array([0.0])) == pytest.approx(0.0)
    assert _logits_to_score(np.array([-2.0, 2.0])) > 0.9
    assert _logits_to_score(np.array([2.0, -2.0])) < 0.1
    with pytest.raises(ValueError):
        _logits_to_score(np.array([float("nan")]))

    assert aggregate_frame_scores([0.1, 0.9, 0.2]) == pytest.approx(0.2)
    assert aggregate_frame_scores([]) is None
    assert classify_score(0.7, faces_detected=5, min_faces=3, threshold=0.5) == ("suspected", "ok")
    assert classify_score(0.3, faces_detected=5, min_faces=3, threshold=0.5) == ("not_detected", "ok")
    assert classify_score(0.9, faces_detected=2, min_faces=3, threshold=0.5) == ("insufficient_data", "low_face_count")
    assert classify_score(None, faces_detected=0, min_faces=3, threshold=0.5) == ("insufficient_data", "no_face")


# ---- 3 chế độ của DeepfakeService qua result_guard ----

def test_mode2_preprocessed_without_model_returns_planned_and_passes_guard(tmp_path: Path):
    settings = _make_settings()
    _create_frames(tmp_path, "exp_m2", 5)
    svc = DeepfakeService()
    ctx = RunContext(settings, "exp_m2", tmp_path, timeout_seconds=10.0)

    result = svc.run(ctx, svc.validate({"asset_id": "med_sample1", "frame_limit": 5}, settings))
    assert result.result_kind == "planned"
    assert [c.name for c in result.components] == ["preprocess", "detector"]
    assert [c.kind for c in result.components] == ["real", "planned"]
    assert result.metrics["detector"] == "not_integrated"
    assert result.metrics["frames_extracted"] == 5
    assert not {"suspicion_score", "verdict"} & set(result.metrics)
    assert len(result.artifacts) == 5 and all(a.startswith("experiments/exp_m2/frames/") for a in result.artifacts)
    assert run_guard(result, "simulated", settings).passed


@pytest.mark.parametrize(
    "scores,bright,expected_verdict,expect_score",
    [
        ([0.82, 0.78, 0.91, 0.85], True, "suspected", True),
        ([0.12, 0.18, 0.25, 0.15], True, "not_detected", True),
        ([0.90, 0.95, 0.92, 0.88], False, "insufficient_data", False),
    ],
)
def test_mode3_detector_verdicts_pass_guard(tmp_path: Path, scores, bright, expected_verdict, expect_score):
    settings = _make_settings(deepfake_threshold=0.5, deepfake_min_faces=3)
    _create_frames(tmp_path, "exp_m3", 4, bright=bright)
    svc = DeepfakeService(scorer=_StubScorer(scores), face_detector=_StubFaceDetector())
    ctx = RunContext(settings, "exp_m3", tmp_path, timeout_seconds=10.0)

    result = svc.run(ctx, svc.validate({"asset_id": "med_sample2", "frame_limit": 4}, settings))
    assert result.result_kind == "real"
    assert [c.kind for c in result.components] == ["real", "real"]
    assert result.metrics["verdict"] == expected_verdict
    assert (result.metrics["suspicion_score"] is not None) is expect_score
    assert not {"probability", "accuracy", "confidence_pct"} & set(result.metrics)
    assert any("không phải kết luận chắc chắn" in item.lower() for item in result.limitations)

    # Đạt trần real, bị từ chối ở trần simulated (G2)
    assert run_guard(result, "real", settings).passed
    assert not run_guard(result, "simulated", settings).passed


def test_mode3_missing_model_file_raises_dep_unavailable(tmp_path: Path):
    settings = _make_settings(deepfake_model_path=str(tmp_path / "does_not_exist.onnx"))
    _create_frames(tmp_path, "exp_missing", 3)
    svc = DeepfakeService()
    ctx = RunContext(settings, "exp_missing", tmp_path, timeout_seconds=10.0)
    with pytest.raises(LabRunError) as exc:
        svc.run(ctx, svc.validate({"asset_id": "med_sample3"}, settings))
    assert exc.value.code == "DEP_UNAVAILABLE"


def test_detector_respects_timeout_cancellation(tmp_path: Path):
    settings = _make_settings()
    exp_dir = _create_frames(tmp_path, "exp_cancel", 4)
    ctx = RunContext(settings, "exp_cancel", tmp_path, timeout_seconds=-1.0)
    with pytest.raises(JobCancelled):
        analyze_experiment_dir(
            exp_dir,
            ctx,
            frame_limit=4,
            scorer=_StubScorer([0.5]),
            face_detector=_StubFaceDetector(),
        )


# ---- Đánh giá tập test thật/giả & báo cáo FPR/FNR/Precision ----

def test_evaluate_predictions_fpr_fnr_precision_and_markdown():
    rows = [
        VideoEvalRow("fake_1.mp4", "synthetic", "face_swap", 24, 20, 0.85, "suspected", 410),     # TP
        VideoEvalRow("fake_2.mp4", "synthetic", "lip_sync", 24, 18, 0.30, "not_detected", 390),   # FN
        VideoEvalRow("orig_1.mp4", "original", "none", 24, 22, 0.15, "not_detected", 400),        # TN
        VideoEvalRow("orig_2.mp4", "original", "none", 24, 19, 0.72, "suspected", 420),           # FP
        VideoEvalRow("dark_1.mp4", "synthetic", "face_swap", 24, 0, None, "insufficient_data", 180, "no_face"),
    ]
    m = evaluate_predictions(rows, threshold=0.5)
    assert (m.tp, m.fp, m.tn, m.fn, m.insufficient_count) == (1, 1, 1, 1, 1)
    assert m.evaluated == 4 and m.total_videos == 5
    assert m.fpr == pytest.approx(0.5)
    assert m.fnr == pytest.approx(0.5)
    assert m.precision == pytest.approx(0.5)
    assert m.false_positives == ("orig_2.mp4",)
    assert m.false_negatives == ("fake_2.mp4",)

    sweep = sweep_thresholds(rows, (0.2, 0.5, 0.8))
    assert len(sweep) == 3
    # Ở ngưỡng 0.8: fake_1=0.85 (TP), orig_2=0.72 (TN) -> FP=0, Precision=1.0
    assert sweep[2].fp == 0 and sweep[2].precision == pytest.approx(1.0)

    md = render_evaluation_markdown(
        rows, m, model_name="demo-onnx", model_sha256="b" * 64, integrated=True, threshold_sweep=sweep
    )
    assert "FPR" in md and "FNR" in md and "Precision" in md
    assert "fake_1.mp4" in md and "orig_2.mp4" in md
    assert "Không phải kết luận chắc chắn" in md

    md_unintegrated = render_evaluation_markdown([], None, integrated=False, not_integrated_note="Chưa kịp huấn luyện.")
    assert "not_integrated" in md_unintegrated and "Chưa kịp huấn luyện." in md_unintegrated


def test_load_manifest_enforces_consent_ref(tmp_path: Path):
    good_csv = tmp_path / "MANIFEST.csv"
    good_csv.write_text(
        "filename,label,source,consent_ref,technique,notes\n"
        "# comment line\n"
        "orig_01.mp4,original,Webcam,CONSENT-01,none,ok\n"
        "fake_01.mp4,synthetic,FF++,FFPP-2026,face_swap,c23\n",
        encoding="utf-8",
    )
    items = load_manifest(good_csv)
    assert len(items) == 2 and items[0].label == "original" and items[1].consent_ref == "FFPP-2026"

    bad_csv = tmp_path / "BAD.csv"
    bad_csv.write_text(
        "filename,label,source,consent_ref\norig_01.mp4,original,Webcam,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="consent_ref"):
        load_manifest(bad_csv)
