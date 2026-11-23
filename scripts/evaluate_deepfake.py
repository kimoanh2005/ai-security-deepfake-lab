"""CLI đánh giá mô hình Deepfake trên bộ test thật/giả của TV3.

Đầu vào:
- `--manifest`: đường dẫn `datasets/demo/MANIFEST.csv` (kiểm bắt buộc `consent_ref`, `label`, `source`).
- `--frames-root`: thư mục chứa các thư mục con theo tên mẫu (hoặc `stem` của filename), mỗi thư mục chứa `frames/*.jpg`
  (hoặc trực tiếp các ảnh `.jpg`/`.png`) đúng định dạng đầu ra TV2 → TV3.
- `--model-path`: tệp `.onnx` cục bộ (kèm `--model-sha256` để kiểm toàn vẹn nếu có).

Đầu ra:
- Bảng kết quả từng video (`--out-csv`)
- Báo cáo Markdown gồm FPR / FNR / Precision, khảo sát ngưỡng, ca sai FP/FN, giới hạn (`--out-report`).
- Hỗ trợ cờ `--not-integrated` để xuất báo cáo ghi chú chuẩn khi chưa tích hợp mô hình (FR-DF-04 / MỞ-6).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import Settings  # noqa: E402
from app.services.base import RunContext  # noqa: E402
from app.services.deepfake_detector import (  # noqa: E402
    OnnxDeepfakeScorer,
    OpenCVFaceDetector,
    VideoEvalRow,
    analyze_experiment_dir,
    evaluate_predictions,
    load_manifest,
    render_evaluation_markdown,
    sweep_thresholds,
)


def _find_sample_exp_dir(frames_root: Path, filename: str) -> Path | None:
    """Hỗ trợ cấu trúc `<frames_root>/<stem>/frames/` hoặc `<frames_root>/<filename>/frames/`."""
    stem = Path(filename).stem
    for candidate in (frames_root / stem, frames_root / filename):
        if (candidate / "frames").is_dir():
            return candidate
    return None


def _write_csv(path: Path, rows: list[VideoEvalRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "filename",
                "ground_truth",
                "technique",
                "frames_analyzed",
                "faces_detected",
                "suspicion_score",
                "verdict",
                "quality",
                "inference_ms",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.filename,
                    r.ground_truth,
                    r.technique,
                    r.frames_analyzed,
                    r.faces_detected,
                    "" if r.suspicion_score is None else f"{r.suspicion_score:.4f}",
                    r.verdict,
                    r.quality,
                    r.inference_ms,
                ]
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Đánh giá mô hình Deepfake (TV3) — xuất bảng kết quả & FPR/FNR/Precision.")
    parser.add_argument("--manifest", type=Path, default=ROOT / "datasets" / "demo" / "MANIFEST.csv")
    parser.add_argument("--frames-root", type=Path, default=ROOT / "datasets" / "demo" / "extracted")
    parser.add_argument("--model-path", type=Path, default=None)
    parser.add_argument("--model-name", type=str, default="")
    parser.add_argument("--model-sha256", type=str, default="")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--min-faces", type=int, default=3)
    parser.add_argument("--frame-limit", type=int, default=24)
    parser.add_argument("--input-size", type=int, default=224)
    parser.add_argument("--not-integrated", action="store_true", help="Xuất báo cáo ghi chú khi chưa tích hợp detector.")
    parser.add_argument("--note", type=str, default="", help="Ghi chú lý do chưa tích hợp (dùng cùng --not-integrated).")
    parser.add_argument("--out-report", type=Path, default=ROOT / "docs" / "deepfake-evaluation-report.md")
    parser.add_argument("--out-csv", type=Path, default=ROOT / "datasets" / "demo" / "results.csv")
    args = parser.parse_args(argv)

    if args.not_integrated:
        report = render_evaluation_markdown([], None, integrated=False, not_integrated_note=args.note)
        args.out_report.parent.mkdir(parents=True, exist_ok=True)
        args.out_report.write_text(report, encoding="utf-8")
        print(f"Đã xuất báo cáo trạng thái chưa tích hợp: {args.out_report}")
        return 0

    if args.model_path is None:
        parser.error("Cần truyền --model-path (hoặc dùng --not-integrated nếu chưa tích hợp mô hình).")

    items = load_manifest(args.manifest)
    if not items:
        raise SystemExit(f"MANIFEST.csv chưa có dòng mẫu hợp lệ nào: {args.manifest}")

    scorer = OnnxDeepfakeScorer(
        args.model_path,
        model_name=args.model_name,
        expected_sha256=args.model_sha256,
        input_size=args.input_size,
    )
    face_detector = OpenCVFaceDetector()
    settings = Settings(
        _env_file=None,
        jwt_secret="eval-cli-placeholder-0123456789abcdef",
        deepfake_threshold=args.threshold,
        deepfake_min_faces=args.min_faces,
        max_frames=args.frame_limit,
    )

    rows: list[VideoEvalRow] = []
    for item in items:
        exp_dir = _find_sample_exp_dir(args.frames_root, item.filename)
        if exp_dir is None:
            print(f"[BỎ QUA] Không tìm thấy thư mục frames/ cho mẫu: {item.filename}", file=sys.stderr)
            continue
        ctx = RunContext(settings, f"eval_{Path(item.filename).stem}", args.frames_root, timeout_seconds=300.0)
        outcome = analyze_experiment_dir(
            exp_dir,
            ctx,
            frame_limit=args.frame_limit,
            scorer=scorer,
            face_detector=face_detector,
            threshold=args.threshold,
            min_faces=args.min_faces,
        )
        rows.append(
            VideoEvalRow(
                filename=item.filename,
                ground_truth=item.label,
                technique=item.technique,
                frames_analyzed=outcome.frames_analyzed,
                faces_detected=outcome.faces_detected,
                suspicion_score=outcome.suspicion_score,
                verdict=outcome.verdict,
                inference_ms=outcome.inference_ms,
                quality=outcome.quality,
            )
        )

    metrics = evaluate_predictions(rows, args.threshold)
    sweep = sweep_thresholds(rows)
    _write_csv(args.out_csv, rows)
    report = render_evaluation_markdown(
        rows,
        metrics,
        model_name=scorer.name,
        model_sha256=scorer.sha256,
        integrated=True,
        threshold_sweep=sweep,
    )
    args.out_report.parent.mkdir(parents=True, exist_ok=True)
    args.out_report.write_text(report, encoding="utf-8")
    print(f"Đã xuất bảng kết quả: {args.out_csv}")
    print(f"Đã xuất báo cáo đánh giá: {args.out_report}")
    print(
        f"Tóm tắt (threshold={metrics.threshold}): evaluated={metrics.evaluated}/{metrics.total_videos}, "
        f"TP={metrics.tp}, FP={metrics.fp}, TN={metrics.tn}, FN={metrics.fn}, "
        f"FPR={metrics.fpr}, FNR={metrics.fnr}, Precision={metrics.precision}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
