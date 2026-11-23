# Hướng dẫn & Báo cáo Mô hình Deepfake (TV3)

| | |
|---|---|
| **Phụ trách** | TV3 — AI Deepfake: mô hình & đánh giá |
| **Liên kết đặc tả** | `docs/spec/spec.md` (FR-DF-02, FR-DF-04, FR-DF-05, FR-DF-06, mục 6 & 9.3), `docs/integration.md` |
| **Mã nguồn chính** | `backend/app/services/deepfake_detector.py`, `backend/app/services/labs/deepfake.py`, `scripts/evaluate_deepfake.py` |

---

## 1. Kiến trúc dịch vụ detector (TV2 → TV3 → TV1)

```text
storage/experiments/<experiment_id>/     (TV2 bàn giao)
├─ metadata.json
├─ frames/000001.jpg ...
└─ audio.wav (không phân tích ở MVP - D-03)
          │
          ▼
app/services/deepfake_detector.py        (TV3)
├─ verify_model_sha256()                 kiểm toàn vẹn trọng số (Cổng 2)
├─ OpenCVFaceDetector                    cắt vùng khuôn mặt lớn nhất
├─ OnnxDeepfakeScorer                    chạy ONNX (onnxruntime hoặc cv2.dnn)
├─ aggregate_frame_scores()              trung vị điểm các khung hình có mặt
└─ classify_score()                      suspected | not_detected | insufficient_data
          │
          ▼
app/services/labs/deepfake.py            (LabService)
└─ Trả LabResult → result_guard (G1–G7) → SQLite (TV1)
```

## 2. Ba trạng thái vận hành trung thực

| Điều kiện | `components` | `result_kind` | `metrics` |
|---|---|---|---|
| Chưa có `frames/` từ TV2 (stub) | `pipeline: simulated` | `simulated` | `detector: "not_integrated"`, không có `suspicion_score`/`verdict` |
| Có `frames/` từ TV2, `DEEPFAKE_MODEL_PATH` rỗng (FR-DF-04) | `preprocess: real`, `detector: planned` | `planned` | `detector: "not_integrated"`, `frames_extracted`, không có `suspicion_score`/`verdict` |
| Có `frames/` từ TV2 + tệp `.onnx` hợp lệ (FR-DF-05) | `preprocess: real`, `detector: real` | `real` *(cần admin nâng `labs.status`)* | `suspicion_score`, `threshold`, `verdict`, `frames_analyzed`, `faces_detected`, `quality`, `inference_ms` |

- Tuyệt đối **không** đặt khóa `probability`, `accuracy`, `confidence_pct` ở cấp trên cùng của `metrics` (luật **G4**).
- Mọi kết quả luôn kèm cảnh báo cố định **FR-DF-06**: *"Không phải kết luận chắc chắn về danh tính hoặc sự thật của video."*

## 3. Cách kích hoạt mô hình ONNX trên máy cục bộ

1. Tải tệp trọng số mô hình phân loại mặt Deepfake định dạng `.onnx` (đầu vào NCHW `1×3×224×224` chuẩn ImageNet RGB, đầu ra 1 logit/score hoặc 2 logits `[original, synthetic]`), lưu vào `backend/models/deepfake.onnx` (thư mục/đuôi `.onnx` đã được `.gitignore` loại trừ, không commit).
2. Tính mã SHA-256 của tệp trọng số:
   ```powershell
   Get-FileHash backend\models\deepfake.onnx -Algorithm SHA256
   ```
3. Điền vào `backend/.env`:
   ```env
   DEEPFAKE_MODEL_PATH=./models/deepfake.onnx
   DEEPFAKE_MODEL_NAME=efficientnet-b0-ffpp-v1
   DEEPFAKE_MODEL_SHA256=<chuỗi 64 ký tự hex chữ thường>
   DEEPFAKE_THRESHOLD=0.5
   DEEPFAKE_MIN_FACES=3
   DEEPFAKE_INPUT_SIZE=224
   ```
4. Sau khi có báo cáo đánh giá FPR/FNR/Precision bên dưới, `admin` nâng trần Lab:
   `PATCH /api/v1/labs/deepfake` với `{"status": "real"}`.

## 4. Quy trình đánh giá bộ test thật/giả (FPR / FNR / Precision)

1. Điền danh sách mẫu vào `datasets/demo/MANIFEST.csv` (bắt buộc đủ `filename`, `label`, `source`, `consent_ref`).
2. Dùng pipeline tiền xử lý của TV2 trích khung hình vào `datasets/demo/extracted/<ten_mau>/frames/`.
3. Chạy lệnh đánh giá:
   ```powershell
   backend\.venv\Scripts\python.exe scripts\evaluate_deepfake.py `
     --manifest datasets\demo\MANIFEST.csv `
     --frames-root datasets\demo\extracted `
     --model-path backend\models\deepfake.onnx `
     --model-name "efficientnet-b0-ffpp-v1" `
     --model-sha256 "<sha256>" `
     --threshold 0.5 `
     --out-report docs\deepfake-evaluation-report.md `
     --out-csv datasets\demo\results.csv
   ```
4. Nộp `datasets/demo/results.csv` và `docs/deepfake-evaluation-report.md` kèm kết quả khảo sát ngưỡng.

## 5. Ghi chú khi chưa tích hợp (MỞ-6 / FR-DF-04)

Nếu chốt phương án chưa tích hợp mô hình do giới hạn phần cứng hoặc chất lượng mô hình chưa đạt trên bộ test thật/giả:
- Để trống `DEEPFAKE_MODEL_PATH` trong `.env`.
- Giữ trần `labs.status = "simulated"` (kết quả khi có khung hình từ TV2 sẽ tự trả `result_kind = "planned"`, `detector = "not_integrated"`, qua cổng `result_guard` hợp lệ).
- Xuất báo cáo ghi chú chính thức bằng cờ `--not-integrated` của `scripts/evaluate_deepfake.py`.
