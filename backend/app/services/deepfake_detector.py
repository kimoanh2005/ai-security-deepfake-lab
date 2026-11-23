"""TV3 — Mô-đun suy luận & đánh giá Deepfake (SPEC 9.3, FR-DF-04/05/06).

Quy tắc phụ thuộc (SPEC 5.4):
- KHÔNG import `app.api`, `app.models`, `app.jobs`; không chạm CSDL; không tải trọng số từ mạng lúc chạy.
- Nhận thư mục đầu ra của TV2 (`storage/experiments/<experiment_id>/`):
    ├─ metadata.json   {duration_s, fps, width, height, codec, frame_count, has_audio}
    ├─ frames/000001.jpg …
    └─ audio.wav       (MVP không phân tích âm thanh — D-03)
- Hỗ trợ chạy mô hình ONNX cục bộ qua `onnxruntime` (nếu cài) hoặc `cv2.dnn.readNetFromONNX` (có sẵn trong opencv-python),
  kèm kiểm tra SHA-256 trọng số (Cổng 2) và `ctx.check()` ở mỗi khung hình.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import math
import statistics
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

import cv2
import numpy as np

from app.services.base import RunContext

VALID_VERDICTS: tuple[str, ...] = ("suspected", "not_detected", "insufficient_data")
VALID_LABELS: tuple[str, ...] = ("original", "synthetic")
IMAGE_EXTS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png"})

FIXED_WARNING = "Không phải kết luận chắc chắn về danh tính hoặc sự thật của video."
AUDIO_LIMITATION = "MVP chưa phân tích âm thanh (D-03); nếu video có luồng âm thanh chỉ lưu tách riêng."
NOT_INTEGRATED_LIMITATION = (
    "Chưa có phát hiện tự động (detector: not_integrated); không có điểm số hay nhãn giả/thật."
)

# Chuẩn hóa ImageNet phổ biến cho các mô hình phân loại mặt (EfficientNet / Xception / MobileNet ONNX)
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class ModelLoadError(RuntimeError):
    """Lỗi nạp hoặc kiểm tra toàn vẹn tệp trọng số mô hình."""


@dataclass(frozen=True)
class DetectionOutcome:
    """Kết quả suy luận cấp video của TV3 (tách khỏi LabResult để dễ tái sử dụng trong script đánh giá)."""

    suspicion_score: float | None
    threshold: float
    verdict: str
    frames_analyzed: int
    faces_detected: int
    quality: str
    model_name: str
    model_sha256: str
    inference_ms: int
    frame_scores: tuple[float, ...] = ()
    artifacts: tuple[str, ...] = ()
    has_audio: bool = False


class FaceDetector(Protocol):
    """Giao diện cắt vùng khuôn mặt lớn nhất từ ảnh BGR; trả None nếu không phát hiện được mặt."""

    def extract_face(self, bgr: np.ndarray) -> np.ndarray | None: ...


class FrameScorer(Protocol):
    """Giao diện chấm điểm nghi ngờ (0.0 = ít nghi ngờ .. 1.0 = nghi ngờ cao) cho một ảnh mặt BGR."""

    name: str
    sha256: str

    def score_face(self, face_bgr: np.ndarray) -> float: ...


# ---- Kiểm tra toàn vẹn tệp trọng số (Cổng 2) ----

def compute_file_sha256(path: Path) -> str:
    """Tính SHA-256 hex theo luồng (không nạp toàn bộ tệp trọng số lớn vào RAM)."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model_sha256(path: Path, expected_sha256: str = "") -> str:
    """Xác minh tệp trọng số tồn tại và khớp SHA-256 (nếu cấu hình). Trả mã SHA-256 thực tế."""
    if not path.is_file():
        raise ModelLoadError("Không tìm thấy tệp trọng số mô hình Deepfake đã cấu hình.")
    actual = compute_file_sha256(path)
    expected = expected_sha256.strip().lower()
    if expected:
        if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
            raise ModelLoadError("DEEPFAKE_MODEL_SHA256 không đúng định dạng hex 64 ký tự.")
        if not hmac.compare_digest(actual, expected):
            raise ModelLoadError("SHA-256 của tệp trọng số mô hình không khớp cấu hình.")
    return actual


# ---- Phát hiện & cắt khuôn mặt (OpenCV tích hợp sẵn) ----

class OpenCVFaceDetector:
    """Phát hiện khuôn mặt bằng Haar Cascade có sẵn trong `opencv-python` (chạy CPU thuần, không tải mạng)."""

    def __init__(self, margin: float = 0.2, min_size: int = 40) -> None:
        self._margin = margin
        self._min_size = min_size
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        if not cascade_path.is_file():
            raise ModelLoadError("Thiếu tệp haarcascade_frontalface_default.xml trong gói OpenCV.")
        self._cascade = cv2.CascadeClassifier(str(cascade_path))
        if self._cascade.empty():
            raise ModelLoadError("Không nạp được bộ phát hiện khuôn mặt OpenCV.")

    def extract_face(self, bgr: np.ndarray) -> np.ndarray | None:
        if bgr is None or bgr.size == 0:
            return None
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=4,
            minSize=(self._min_size, self._min_size),
        )
        if len(faces) == 0:
            return None
        # Chọn khuôn mặt có diện tích lớn nhất trong khung hình
        x, y, w, h = max(faces, key=lambda box: int(box[2]) * int(box[3]))
        img_h, img_w = bgr.shape[:2]
        pad_x = int(w * self._margin)
        pad_y = int(h * self._margin)
        x1 = max(0, int(x) - pad_x)
        y1 = max(0, int(y) - pad_y)
        x2 = min(img_w, int(x) + int(w) + pad_x)
        y2 = min(img_h, int(y) + int(h) + pad_y)
        crop = bgr[y1:y2, x1:x2]
        return crop if crop.size > 0 else None


# ---- Suy luận ONNX (ưu tiên onnxruntime nếu có, fallback cv2.dnn) ----

def _logits_to_score(raw: np.ndarray) -> float:
    """Chuyển đầu ra mô hình (1 logit/score hoặc 2 logits [original, synthetic]) về thang [0.0, 1.0]."""
    arr = np.asarray(raw, dtype=np.float64).reshape(-1)
    if arr.size == 0 or not np.all(np.isfinite(arr)):
        raise ValueError("Đầu ra mô hình rỗng hoặc chứa NaN/Infinity.")
    if arr.size == 1:
        val = float(arr[0])
        if 0.0 <= val <= 1.0:
            return val
        # Sigmoid ổn định số học
        return float(1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, val)))))
    # 2 lớp: giả định chỉ số 1 là lớp nghi ngờ giả mạo (synthetic)
    pair = arr[:2]
    shifted = pair - float(np.max(pair))
    exps = np.exp(np.clip(shifted, -60.0, 0.0))
    score = float(exps[1] / np.sum(exps))
    return max(0.0, min(1.0, score))


def _preprocess_face(face_bgr: np.ndarray, input_size: int) -> np.ndarray:
    """Chuẩn hóa ảnh mặt BGR -> tensor NCHW float32 chuẩn ImageNet."""
    rgb = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (input_size, input_size), interpolation=cv2.INTER_AREA)
    arr = resized.astype(np.float32) / 255.0
    arr = (arr - _IMAGENET_MEAN) / _IMAGENET_STD
    return np.transpose(arr, (2, 0, 1))[np.newaxis, ...].astype(np.float32)


class OnnxDeepfakeScorer:
    """Nạp và chạy suy luận mô hình ONNX cục bộ (có kiểm tra SHA-256)."""

    def __init__(
        self,
        model_path: Path,
        *,
        model_name: str = "",
        expected_sha256: str = "",
        input_size: int = 224,
    ) -> None:
        resolved = model_path.resolve()
        self.sha256 = verify_model_sha256(resolved, expected_sha256)
        self.name = model_name.strip() or resolved.stem
        self._input_size = input_size
        self._lock = threading.Lock()
        self._ort_session = None
        self._ort_input_name = ""
        self._cv_net = None

        try:
            import onnxruntime as ort  # type: ignore[import-not-found]

            self._ort_session = ort.InferenceSession(str(resolved), providers=["CPUExecutionProvider"])
            self._ort_input_name = self._ort_session.get_inputs()[0].name
            self.backend = "onnxruntime"
        except ImportError:
            try:
                self._cv_net = cv2.dnn.readNetFromONNX(str(resolved))
                self.backend = "opencv_dnn"
            except cv2.error as exc:
                raise ModelLoadError("Không nạp được tệp ONNX bằng OpenCV DNN.") from exc
        except Exception as exc:  # noqa: BLE001
            raise ModelLoadError("Không khởi tạo được phiên suy luận ONNX.") from exc

    def score_face(self, face_bgr: np.ndarray) -> float:
        blob = _preprocess_face(face_bgr, self._input_size)
        with self._lock:
            if self._ort_session is not None:
                out = self._ort_session.run(None, {self._ort_input_name: blob})[0]
            else:
                self._cv_net.setInput(blob)
                out = self._cv_net.forward()
        score = _logits_to_score(out)
        if not math.isfinite(score):
            raise ValueError("Điểm khung hình không hữu hạn.")
        return round(float(score), 6)


_SCORER_CACHE: dict[tuple[str, str, int], OnnxDeepfakeScorer] = {}
_CACHE_LOCK = threading.Lock()


def get_cached_scorer(
    model_path: Path,
    *,
    model_name: str = "",
    expected_sha256: str = "",
    input_size: int = 224,
) -> OnnxDeepfakeScorer:
    """Giữ một phiên suy luận ONNX theo (đường dẫn, sha256, kích thước) để tránh nạp lại mỗi job."""
    key = (str(model_path.resolve()), expected_sha256.strip().lower(), input_size)
    with _CACHE_LOCK:
        scorer = _SCORER_CACHE.get(key)
        if scorer is None:
            scorer = OnnxDeepfakeScorer(
                model_path,
                model_name=model_name,
                expected_sha256=expected_sha256,
                input_size=input_size,
            )
            _SCORER_CACHE[key] = scorer
        return scorer


def clear_scorer_cache() -> None:
    """Dọn cache scorer (dùng trong kiểm thử)."""
    with _CACHE_LOCK:
        _SCORER_CACHE.clear()


# ---- Đọc thư mục bàn giao TV2 → TV3 & tổng hợp điểm cấp video ----

def list_frame_files(exp_dir: Path, frame_limit: int) -> list[Path]:
    """Liệt kê tối đa `frame_limit` ảnh khung hình trong `exp_dir/frames/` theo thứ tự ổn định."""
    frames_dir = exp_dir / "frames"
    if not frames_dir.is_dir():
        return []
    root = frames_dir.resolve()
    files: list[Path] = []
    for p in sorted(frames_dir.iterdir(), key=lambda item: item.name):
        if p.suffix.lower() not in IMAGE_EXTS:
            continue
        resolved = p.resolve()
        # Không đi theo symlink thoát khỏi thư mục frames/
        if not resolved.is_relative_to(root) or not resolved.is_file():
            continue
        files.append(resolved)
        if len(files) >= frame_limit:
            break
    return files


def read_preprocess_metadata(exp_dir: Path) -> dict[str, Any]:
    """Đọc `metadata.json` do TV2 xuất (nếu tồn tại và hợp lệ)."""
    meta_file = exp_dir / "metadata.json"
    if not meta_file.is_file():
        return {}
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def aggregate_frame_scores(scores: Sequence[float]) -> float | None:
    """Tổng hợp điểm khung hình thành điểm nghi ngờ cấp video bằng trung vị (bền vững trước nhiễu 1-2 khung hình)."""
    clean = [float(s) for s in scores if isinstance(s, (int, float)) and not isinstance(s, bool) and math.isfinite(s)]
    if not clean:
        return None
    val = float(statistics.median(clean))
    return round(max(0.0, min(1.0, val)), 4)


def classify_score(score: float | None, *, faces_detected: int, min_faces: int, threshold: float) -> tuple[str, str]:
    """Trả (verdict, quality) theo ngưỡng chốt trước và số khuôn mặt phát hiện được."""
    if faces_detected == 0:
        return "insufficient_data", "no_face"
    if faces_detected < min_faces or score is None:
        return "insufficient_data", "low_face_count"
    verdict = "suspected" if score >= threshold else "not_detected"
    return verdict, "ok"


def _relative_artifact(path: Path, storage_dir: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(storage_dir.resolve())
        return rel.as_posix()
    except ValueError:
        return None


def analyze_experiment_dir(
    exp_dir: Path,
    ctx: RunContext,
    *,
    frame_limit: int,
    scorer: FrameScorer,
    face_detector: FaceDetector | None = None,
    threshold: float | None = None,
    min_faces: int | None = None,
) -> DetectionOutcome:
    """Chạy suy luận Deepfake trên thư mục `storage/experiments/<id>/` của TV2."""
    detector = face_detector or OpenCVFaceDetector()
    used_threshold = float(ctx.settings.deepfake_threshold if threshold is None else threshold)
    used_min_faces = int(ctx.settings.deepfake_min_faces if min_faces is None else min_faces)

    meta = read_preprocess_metadata(exp_dir)
    has_audio = bool(meta.get("has_audio")) or (exp_dir / "audio.wav").is_file()
    frame_paths = list_frame_files(exp_dir, frame_limit)

    started = time.monotonic()
    frame_scores: list[float] = []
    artifacts: list[str] = []
    frames_analyzed = 0

    for frame_path in frame_paths:
        ctx.check()  # bắt buộc trong vòng lặp dài (INT-01 mục 7)
        bgr = cv2.imread(str(frame_path))
        if bgr is None:
            continue
        frames_analyzed += 1
        rel = _relative_artifact(frame_path, ctx.storage_dir)
        if rel:
            artifacts.append(rel)
        face = detector.extract_face(bgr)
        if face is None:
            continue
        score = float(scorer.score_face(face))
        if math.isfinite(score):
            frame_scores.append(round(max(0.0, min(1.0, score)), 6))

    elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
    faces_detected = len(frame_scores)
    # Với video ngắn có ít hơn min_faces khung hình nhưng có đủ mặt ở mọi khung hình, vẫn yêu cầu tối thiểu min_faces
    effective_score = aggregate_frame_scores(frame_scores) if faces_detected >= used_min_faces else None
    verdict, quality = classify_score(
        effective_score,
        faces_detected=faces_detected,
        min_faces=used_min_faces,
        threshold=used_threshold,
    )
    if verdict == "insufficient_data":
        effective_score = None

    return DetectionOutcome(
        suspicion_score=effective_score,
        threshold=round(used_threshold, 4),
        verdict=verdict,
        frames_analyzed=frames_analyzed,
        faces_detected=faces_detected,
        quality=quality,
        model_name=scorer.name,
        model_sha256=scorer.sha256,
        inference_ms=elapsed_ms,
        frame_scores=tuple(frame_scores),
        artifacts=tuple(artifacts),
        has_audio=has_audio,
    )


# ---- Bộ công cụ đánh giá tập test thật/giả & báo cáo FPR/FNR/Precision (TV3) ----

@dataclass(frozen=True)
class ManifestItem:
    filename: str
    label: str           # original | synthetic
    source: str
    consent_ref: str
    technique: str = ""
    notes: str = ""


@dataclass(frozen=True)
class VideoEvalRow:
    filename: str
    ground_truth: str    # original | synthetic
    technique: str
    frames_analyzed: int
    faces_detected: int
    suspicion_score: float | None
    verdict: str         # suspected | not_detected | insufficient_data
    inference_ms: int
    quality: str = "ok"


@dataclass(frozen=True)
class EvaluationMetrics:
    threshold: float
    total_videos: int
    evaluated: int
    insufficient_count: int
    tp: int
    fp: int
    tn: int
    fn: int
    fpr: float | None
    fnr: float | None
    precision: float | None
    avg_inference_ms: float | None
    false_positives: tuple[str, ...] = field(default_factory=tuple)
    false_negatives: tuple[str, ...] = field(default_factory=tuple)


def load_manifest(manifest_path: Path) -> list[ManifestItem]:
    """Đọc `datasets/demo/MANIFEST.csv`; từ chối ngay mẫu thiếu `consent_ref`, `source` hoặc sai `label` (FR-DF-02)."""
    items: list[ManifestItem] = []
    with open(manifest_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"filename", "label", "source", "consent_ref"}
        fieldnames = set(reader.fieldnames or [])
        missing_cols = sorted(required - fieldnames)
        if missing_cols:
            raise ValueError(f"MANIFEST.csv thiếu cột bắt buộc: {missing_cols}")
        for idx, row in enumerate(reader, start=2):
            filename = (row.get("filename") or "").strip()
            if not filename or filename.startswith("#"):
                continue
            label = (row.get("label") or "").strip().lower()
            source = (row.get("source") or "").strip()
            consent_ref = (row.get("consent_ref") or "").strip()
            technique = (row.get("technique") or "").strip()
            notes = (row.get("notes") or "").strip()
            if label not in VALID_LABELS:
                raise ValueError(f"Dòng {idx}: label phải là 'original' hoặc 'synthetic', nhận {label!r}.")
            if not source:
                raise ValueError(f"Dòng {idx} ({filename}): thiếu source.")
            if not consent_ref:
                raise ValueError(f"Dòng {idx} ({filename}): thiếu consent_ref (FR-DF-02).")
            items.append(ManifestItem(filename, label, source, consent_ref, technique, notes))
    return items


def _safe_div(num: int, den: int) -> float | None:
    return round(num / den, 4) if den > 0 else None


def evaluate_predictions(rows: Sequence[VideoEvalRow], threshold: float) -> EvaluationMetrics:
    """Tính TP/FP/TN/FN và FPR, FNR, Precision (dương tính = synthetic / suspected).

    Các video `insufficient_data` được tách riêng vào `insufficient_count`, không ép thành đúng hay sai.
    """
    tp = fp = tn = fn = insufficient = 0
    fps: list[str] = []
    fns: list[str] = []
    latencies: list[int] = []

    for r in rows:
        latencies.append(r.inference_ms)
        if r.verdict == "insufficient_data" or r.suspicion_score is None:
            insufficient += 1
            continue
        predicted_fake = r.suspicion_score >= threshold
        actual_fake = r.ground_truth == "synthetic"
        if actual_fake and predicted_fake:
            tp += 1
        elif not actual_fake and predicted_fake:
            fp += 1
            fps.append(r.filename)
        elif not actual_fake and not predicted_fake:
            tn += 1
        else:
            fn += 1
            fns.append(r.filename)

    evaluated = tp + fp + tn + fn
    avg_ms = round(sum(latencies) / len(latencies), 1) if latencies else None
    return EvaluationMetrics(
        threshold=round(float(threshold), 4),
        total_videos=len(rows),
        evaluated=evaluated,
        insufficient_count=insufficient,
        tp=tp,
        fp=fp,
        tn=tn,
        fn=fn,
        fpr=_safe_div(fp, fp + tn),
        fnr=_safe_div(fn, fn + tp),
        precision=_safe_div(tp, tp + fp),
        avg_inference_ms=avg_ms,
        false_positives=tuple(fps),
        false_negatives=tuple(fns),
    )


def sweep_thresholds(
    rows: Sequence[VideoEvalRow],
    thresholds: Sequence[float] = (0.3, 0.4, 0.5, 0.6, 0.7),
) -> list[EvaluationMetrics]:
    """Đo FPR/FNR/Precision trên nhiều ngưỡng để chọn ngưỡng chốt trước khi tích hợp."""
    return [evaluate_predictions(rows, t) for t in thresholds]


def _fmt_rate(val: float | None) -> str:
    return f"{val:.4f} ({val * 100:.1f}%)" if val is not None else "N/A (chưa đủ mẫu)"


def render_evaluation_markdown(
    rows: Sequence[VideoEvalRow],
    metrics: EvaluationMetrics | None,
    *,
    model_name: str = "",
    model_sha256: str = "",
    integrated: bool = True,
    not_integrated_note: str = "",
    threshold_sweep: Sequence[EvaluationMetrics] = (),
) -> str:
    """Xuất báo cáo bàn giao của TV3: bảng kết quả, FPR/FNR/Precision, ca sai, giới hạn & ghi chú khi chưa tích hợp."""
    lines: list[str] = [
        "# Báo cáo Đánh giá Mô hình Deepfake (TV3)",
        "",
        "## 1. Trạng thái tích hợp",
        "",
    ]
    if not integrated:
        lines += [
            "- **Trạng thái detector:** `not_integrated` (`components.detector = planned`, `result_kind = planned`).",
            f"- **Ghi chú:** {not_integrated_note or 'Chưa có mô hình đạt tiêu chí độ chính xác/hiệu năng trên máy demo Windows để tích hợp.'}",
            "- **Nguyên tắc trung thực (FR-DF-04):** Khi chưa tích hợp, hệ thống **không** xuất `suspicion_score` và **không** gán nhãn giả/thật.",
            "",
        ]
    else:
        short_hash = model_sha256[:12] if model_sha256 else "—"
        lines += [
            f"- **Mô hình:** `{model_name or 'chưa đặt tên'}`",
            f"- **SHA-256 trọng số (12 ký tự đầu):** `{short_hash}`",
            f"- **Ngưỡng chốt trước (`threshold`):** `{metrics.threshold if metrics else '—'}`",
            "",
        ]

    if metrics is not None:
        lines += [
            "## 2. Chỉ số đánh giá (FPR / FNR / Precision)",
            "",
            "| Chỉ số | Giá trị | Ý nghĩa |",
            "|---|---|---|",
            f"| Tổng video trong bộ test | {metrics.total_videos} | Số mẫu trong MANIFEST.csv |",
            f"| Số video đủ dữ liệu chấm (`evaluated`) | {metrics.evaluated} | Loại trừ `insufficient_data` ({metrics.insufficient_count}) |",
            f"| TP / FP / TN / FN | {metrics.tp} / {metrics.fp} / {metrics.tn} / {metrics.fn} | Dương tính = `synthetic` (`suspected`) |",
            f"| **FPR** (`FP / (FP + TN)`) | {_fmt_rate(metrics.fpr)} | Tỷ lệ video thật bị cảnh báo nhầm |",
            f"| **FNR** (`FN / (FN + TP)`) | {_fmt_rate(metrics.fnr)} | Tỷ lệ video giả bị bỏ lọt |",
            f"| **Precision** (`TP / (TP + FP)`) | {_fmt_rate(metrics.precision)} | Độ chính xác trong các ca bị cảnh báo |",
            f"| Thời gian suy luận TB (ms/video) | {metrics.avg_inference_ms if metrics.avg_inference_ms is not None else '—'} | Căn cứ đặt `JOB_TIMEOUT_DEEPFAKE` |",
            "",
        ]

    if threshold_sweep:
        lines += [
            "## 3. Khảo sát ngưỡng (`threshold`)",
            "",
            "| Ngưỡng | TP | FP | TN | FN | FPR | FNR | Precision |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for m in threshold_sweep:
            lines.append(
                f"| {m.threshold:.2f} | {m.tp} | {m.fp} | {m.tn} | {m.fn} "
                f"| {_fmt_rate(m.fpr)} | {_fmt_rate(m.fnr)} | {_fmt_rate(m.precision)} |"
            )
        lines.append("")

    if rows:
        lines += [
            "## 4. Bảng kết quả từng video",
            "",
            "| Tệp video | Nhãn gốc | Kỹ thuật | Khung hình | Có mặt | `suspicion_score` | `verdict` | Thời gian (ms) |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            score_txt = f"{r.suspicion_score:.4f}" if r.suspicion_score is not None else "—"
            lines.append(
                f"| `{r.filename}` | {r.ground_truth} | {r.technique or '—'} | {r.frames_analyzed} "
                f"| {r.faces_detected} | {score_txt} | `{r.verdict}` | {r.inference_ms} |"
            )
        lines.append("")

    if metrics is not None and (metrics.false_positives or metrics.false_negatives):
        lines += [
            "## 5. Các ca nhận diện sai (FP / FN)",
            "",
            f"- **False Positives (thật bị gắn `suspected`):** {', '.join(f'`{x}`' for x in metrics.false_positives) or 'Không có'}",
            f"- **False Negatives (giả bị bỏ lọt `not_detected`):** {', '.join(f'`{x}`' for x in metrics.false_negatives) or 'Không có'}",
            "",
        ]

    lines += [
        "## 6. Giới hạn bắt buộc (FR-DF-06, D-03)",
        "",
        f"- {FIXED_WARNING}",
        f"- {AUDIO_LIMITATION}",
        "- Kết quả phụ thuộc chất lượng video (độ phân giải, nén lại nhiều lần, ánh sáng, góc nghiêng khuôn mặt).",
        "- Mô hình có thể giảm mạnh độ tin cậy với các kỹ thuật sinh/ghép mặt mới chưa xuất hiện trong tập huấn luyện.",
        "",
    ]
    return "\n".join(lines)
