# AI Security & Deepfake Lab

Ứng dụng học tập về AI Security và Deepfake, gồm backend FastAPI và frontend React/Vite. Giao diện TV5 hiện lấy danh sách Lab thật từ API, có trạng thái tải/lỗi, Dashboard, danh sách và trang thông tin từng Lab. Những chức năng chưa có API (upload, chạy detector/chatbot, lưu lịch sử) được ghi rõ là chưa tích hợp; giao diện không tạo kết quả giả.

## Yêu cầu

- Windows 10/11, PowerShell
- Python 3.10–3.12
- Node.js 20.19+ hoặc 22.12+
- npm đi kèm Node.js

Không cần khóa API hay tài khoản dịch vụ trả phí để khởi động phần cốt lõi.

## Cài đặt lần đầu

Mở hai cửa sổ PowerShell tại thư mục dự án `ai_security_lab` (thư mục chứa `backend`, `frontend`, `docs`).

### 1. Cài backend

Trong cửa sổ thứ nhất:

```powershell
Set-Location .\backend
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Nếu máy đã cài Python 3.11 hoặc 3.12 thay vì 3.10, thay `py -3.10` bằng `py -3.11` hoặc `py -3.12`. Có thể gọi Python trong virtual environment trực tiếp như trên, không cần thay đổi Execution Policy của PowerShell.

Tạo tệp cấu hình và khởi tạo CSDL (vẫn trong `backend`):

```powershell
Copy-Item .env.example .env
# Mở .env, điền JWT_SECRET (chuỗi ngẫu nhiên >= 16 ký tự), ví dụ sinh bằng:
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Nạp dữ liệu khởi tạo (4 Lab + tài khoản admin/user). Mật khẩu lấy từ `SEED_*_PASSWORD` trong `.env`; nếu để trống, lệnh sẽ in mật khẩu ngẫu nhiên MỘT LẦN, hãy lưu lại. Chạy lại nhiều lần vẫn an toàn:

```powershell
.\.venv\Scripts\python.exe -m app.seed
```

Chạy test: `.\.venv\Scripts\python.exe -m pytest -q` (dùng CSDL tạm, không đụng `lab.db`).

`.env` không được commit. Nếu đã chạy migration bản cũ trước đó, xóa `backend/storage/lab.db*` rồi chạy lại `alembic upgrade head` (các migration 0001/0002 đã được chỉnh sửa trước khi hợp nhất).

### 2. Cài frontend

Trong cửa sổ PowerShell thứ hai:

```powershell
Set-Location .\frontend
npm ci
```

Nếu thư mục dự án không có `package-lock.json`, chạy `npm install` thay cho `npm ci`.

## Chạy ứng dụng

### Backend

Từ cửa sổ backend, đang ở `backend`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Hoặc chạy một lệnh từ thư mục dự án (tự chạy `alembic upgrade head`, seed rồi khởi động uvicorn):

```powershell
.\scripts\start.ps1            # thêm -Reload khi phát triển, -NoSeed để bỏ qua seed, -Port 8001 để đổi cổng
```

- API: <http://127.0.0.1:8000>
- Kiểm tra sức khỏe (DB, worker, FFmpeg, Ollama trên Colab; không cần token): <http://127.0.0.1:8000/api/v1/health>
- Tài liệu API Swagger: <http://127.0.0.1:8000/docs>
- Kiểm tra danh sách Lab: <http://127.0.0.1:8000/api/v1/labs>

### Frontend

Từ cửa sổ frontend, đang ở `frontend`:

```powershell
npm run dev
```

Mở địa chỉ được in ra bởi Vite, thông thường <http://localhost:5173>. Frontend chuyển tiếp đường dẫn `/api` sang backend ở `http://127.0.0.1:8000`; hãy để cả hai cửa sổ đang chạy. Dừng từng dịch vụ bằng `Ctrl+C`.

## Kiểm tra nhanh

Với backend đang chạy, gọi API trực tiếp trong PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/
Invoke-RestMethod http://127.0.0.1:8000/api/v1/labs
```

Sau đó mở frontend và kiểm tra:

1. Dashboard tải được danh sách Lab từ backend.
2. Nút tải lại gọi lại API; tắt backend rồi tải lại sẽ hiện trạng thái lỗi và nút thử kết nối lại.
3. Mở danh sách Lab và từng trang thông tin; các chức năng chưa được backend hỗ trợ có nhãn “Chưa tích hợp”.
4. Bật lại backend và dùng thử kết nối lại để xác nhận trạng thái phục hồi.
5. Thu nhỏ cửa sổ trình duyệt để kiểm tra giao diện responsive và thao tác điều hướng.

Lệnh kiểm tra frontend:

```powershell
Set-Location .\frontend
npm run build
npm run lint
```

## Kịch bản demo gợi ý (3–5 phút)

1. Giới thiệu mục tiêu: thực hành bảo mật trong sandbox, không nhắm vào hệ thống bên ngoài.
2. Khởi động backend và frontend; mở Dashboard để chứng minh danh sách Lab đến từ API thật.
3. Mở danh sách Lab và xem trang Deepfake, Prompt Injection, API Security; chỉ rõ chức năng nào đã có API và chức năng nào chưa.
4. Dừng backend, nhấn tải lại để minh họa trạng thái lỗi; khởi động lại và thử kết nối lại.
5. Kết thúc bằng checklist QA, giới hạn hiện tại và các API cần được nhóm backend/AI tích hợp tiếp.

## Phạm vi hiện tại và phối hợp tích hợp

Backend hiện cung cấp `GET /`, `GET /api/v1/labs`. Vì vậy Dashboard, danh sách Lab, điều hướng, trạng thái kết nối và trang thông tin là chức năng chạy thật; upload video, detector deepfake, chatbot Ollama, rate limiting, request log và lịch sử phiên chưa thể chạy từ giao diện cho đến khi các endpoint tương ứng được cung cấp. Trạng thái `active` từ `/api/v1/labs` chỉ có nghĩa Lab được backend khai báo, không khẳng định chức năng Lab đã hoàn thiện.

Hợp đồng danh sách hiện dùng JSON:

```json
[
  { "id": 1, "name": "Deepfake & Identity Spoofing", "status": "active" }
]
```

Khi bổ sung API cho các Lab, thống nhất trước phương thức, đường dẫn, schema request/response, giới hạn upload, lỗi HTTP và định dạng bằng chứng với backend; sau đó kết nối giao diện theo hợp đồng đã kiểm thử.
