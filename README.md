# AI Security & Deepfake Lab

Ứng dụng thực hành gồm FastAPI và React/Vite. Nhánh Diep tích hợp phần TV4:
chatbot tài liệu Ollama, kiểm thử Prompt Injection, so sánh trước/sau,
API Rate Limiting và nhật ký SQLite.

## Khởi động
Tại backend:
~~~powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
~~~
Tại frontend:
~~~powershell
npm ci
npm run dev
~~~
Mở URL Vite in trong terminal. Giao diện chuyển tiếp /api đến backend cổng 8000.
Swagger: http://127.0.0.1:8000/docs.

## Chạy phần AI Security
1. Chạy Ollama trên Colab theo [notebook](docs/Ollama_Colab.ipynb), hoặc dùng Ollama local.
2. Mở Prompt Injection, nhập URL tunnel Colab hoặc http://localhost:11434.
3. Kiểm tra kết nối và chọn model đang có.
4. So sánh trước/sau hoặc chạy bộ 14 ca test.
5. Mở API Security để thử rate limiting.
6. Tab Nhật ký & lịch sử cho phép xem và xuất kết quả.

[Hướng dẫn TV4 và hai kịch bản demo](docs/TV4_AI_SECURITY_LAB.md)
bao gồm cấu hình model, lỗi Colab, cách đánh giá và giới hạn.

## Trạng thái chức năng
- Dashboard và danh sách lab: API thật.
- Prompt Injection và API Security: đã tích hợp thực thi.
- Deepfake: trang giới thiệu, chưa có detector/upload thực thi.
- Data Poisoning: lên kế hoạch.
- SQLite lưu tại backend/storage/security.sqlite3, không commit dữ liệu chạy vào git.
- Không có kết quả mô hình thật được điền sẵn; phải kết nối Ollama để thu bằng chứng.
- Backend chạy localhost, chưa thiết kế cho truy cập công khai.

## Kiểm tra
~~~powershell
# Tại backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
# Tại frontend
npm run build
npm run lint
~~~
Các test backend gọi trực tiếp handler/service và giả lập đầu ra Ollama.
Chúng kiểm tra logic bảo vệ, phản hồi rate limit, lưu dữ liệu và schema;
không thay thế kiểm thử HTTP đầy đủ hoặc đánh giá mô hình thật.

## TV4 experiments

[Experiment, human judging, ASR and HTTP API measurements](docs/TV4_EXPERIMENTS.md).
