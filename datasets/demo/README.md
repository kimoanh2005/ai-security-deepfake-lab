# Bộ dữ liệu kiểm thử Deepfake (`datasets/demo/`)

Thư mục này chứa tệp chỉ mục `MANIFEST.csv` (theo **FR-DF-02**) dùng để kiểm thử và đánh giá mô hình Deepfake của TV3.

## Quy ước bắt buộc
1. **Không commit video nặng hoặc dữ liệu cá nhân chưa được đồng ý lên Git.**
2. Mọi mẫu trong `MANIFEST.csv` bắt buộc có đủ 4 cột:
   - `filename`: tên tệp video (vd `orig_01.mp4`) hoặc tên thư mục chứa khung hình `frames/` tương ứng.
   - `label`: chỉ nhận `original` (video thật) hoặc `synthetic` (video giả).
   - `source`: nguồn dataset / nguồn quay mẫu.
   - `consent_ref`: căn cứ cho phép sử dụng (giấy phép dataset nghiên cứu hoặc biên bản đồng ý của người quay). Mẫu thiếu `consent_ref` bị từ chối tự động.
3. Cột tùy chọn: `technique` (`none`, `face_swap`, `lip_sync`, `diffusion`…) và `notes` (điều kiện ánh sáng, độ nén, ca khó…).

## Cách chạy đánh giá (TV3)
Từ thư mục gốc dự án:
```powershell
backend\.venv\Scripts\python.exe scripts\evaluate_deepfake.py `
  --manifest datasets\demo\MANIFEST.csv `
  --frames-root datasets\demo\extracted `
  --model-path backend\models\deepfake.onnx `
  --model-name "efficientnet-b0-ffpp-v1" `
  --threshold 0.5 `
  --out-report docs\deepfake-evaluation-report.md `
  --out-csv datasets\demo\results.csv
```
Nếu chốt phương án chưa tích hợp mô hình (MỞ-6 / FR-DF-04):
```powershell
backend\.venv\Scripts\python.exe scripts\evaluate_deepfake.py `
  --not-integrated `
  --note "Mô hình thử nghiệm chưa đạt ngưỡng tốc độ/FPR trên máy CPU demo." `
  --out-report docs\deepfake-evaluation-report.md
```
