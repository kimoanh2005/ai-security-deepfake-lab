# HỢP ĐỒNG REST API — AI Security & Deepfake Lab

| | |
|---|---|
| **Tài liệu** | API-01 · Hợp đồng REST API (thay thế mục 8 của SPEC-01 v0.1) |
| **Phiên bản** | 0.13 (`prompt-runs` GET/PATCH; ASR tính lại; xem phụ lục) |
| **Ngày** | 23/11/2026 |
| **Người phụ trách** | TV1 |
| **Đối tượng đọc** | TV2, TV3, TV4 (cài đặt `LabService`), TV5 (gọi API) |
| **Nguồn chuẩn khi mâu thuẫn** | Tài liệu này; sau đó là `/openapi.json` do FastAPI sinh ra từ mã |

Quy ước ưu tiên: **M** bắt buộc MVP · **S** nên có · **C** có thể hoãn.

---

## 0. Trạng thái triển khai và cách dùng tài liệu

Tài liệu mô tả **hợp đồng đích**. Không phải endpoint nào cũng đã có mã; cột *Trạng thái* cho biết cái gì gọi được ngay. Đường dẫn, mã trạng thái và dạng lỗi của các endpoint chưa làm **đã khóa**: TV5 có thể viết client theo tài liệu này, TV2–TV4 chỉ cần cài `validate`/`run` (xem `docs/integration.md`).

| Nhóm | Endpoint | Trạng thái | Việc |
|---|---|---|---|
| Xác thực | `POST /auth/login`, `GET /users/me` | **Đã có** | T-05 |
| Lab | `GET /labs`, `GET /labs/{lab}`, `PATCH /labs/{lab}` | **Đã có** (`links` hiện chỉ có `self`) | T-06 |
| Chạy Lab | `POST /labs/{lab}/experiments` | **Đã có** (202 + `Location` + `Retry-After`; hỗ trợ `Idempotency-Key`, mục 8) | T-08, T-09 |
| Thử nghiệm | `GET /experiments`, `/{id}` (ETag/304), `/{id}/events`, `/{id}/report` | **Đã có** | T-09 |
| Hệ thống | `GET /health` | **Đã có** (6.8) | T-10 |
| Sandbox | `GET/POST /sandbox/echo` | **Đã có** (6.9) | T-12 |
| Media | `POST/GET /media-assets`, `/{id}` (ETag/304), `/{id}/content`, `DELETE` (admin) | **Đã có** (5.1); mẫu `origin=sample` chưa được nạp (cần `datasets/demo/MANIFEST.csv`) | T-13 |
| Kịch bản/ca | `/labs/{lab}/scenarios`, `/labs/prompt_injection/cases` | Chưa có (cần TV4 cung cấp nội dung) | sau T-09 |
| Khác | `artifacts`, `verification`, `DELETE`, `models`, `users` | Chưa có (ưu tiên S/C) | sau MVP |
| Prompt Injection | `GET`/`PATCH /experiments/{id}/prompt-runs` | Đã có | ASR trong `GET /experiments/{id}` và báo cáo được tính lại từ `prompt_runs` sau khi chấm tay (FR-PI-07) |

Hành vi cụ thể của T-09 (ngoài những gì mục 3–8 đã nêu):

- `GET /experiments/{id}/report` khi experiment còn `queued`/`running` → `409 STATE_CONFLICT`; `failed` vẫn có báo cáo (kèm `error`, không có phần kết quả). Báo cáo JSON có dạng `{ "experiment": {…}, "events": […] }`. Phản hồi có `Vary: Accept`.
- `GET /experiments/{id}` trả `ETag` (băm nội dung biểu diễn) và `Cache-Control: no-cache`; `If-None-Match` (kể cả dạng `W/` hoặc `*`) khớp → `304` không thân.
- `links` của experiment hiện chỉ có `self`, `events`, `report`; `prompt_runs`/`artifacts` thêm khi endpoint tồn tại.
- Thiếu hẳn thân yêu cầu ở `POST` → `400 MALFORMED_REQUEST` (thiếu một trường trong thân vẫn là `422`).
- `result_kind` chỉ lọc được trên experiment đã `done` (kết quả bị guard từ chối không có nhãn).
- **Chưa làm:** `DELETE`, `artifacts`, `verification`.

Đối chiếu với `/openapi.json` (19/11/2026): 5 endpoint đã có khớp tài liệu về đường dẫn, phương thức, mã thành công, `operation_id` (`login`, `get_current_user`, `list_labs`, `get_lab`, `update_lab`) và dạng lỗi. Hai chỗ lệch cần biết:

1. ~~OpenAPI mô tả lỗi `422` bằng `HTTPValidationError`~~ — **đã sửa ở v0.12**: `/openapi.json` mô tả mọi lỗi 4xx/5xx của `/api/v1` bằng `application/problem+json` (schema `ProblemDetails`, `FieldError`), kèm `code` trong mô tả từng mã. Lỗi suy ra tự động từ route (401/403/404/400/422/429/500; `WWW-Authenticate` và `Retry-After` được ghi ở header), lỗi riêng của endpoint (409, 413, 415, 503…) khai báo ở decorator. Endpoint mới được ghi lỗi chung mà không phải làm gì thêm; chỉ cần khai báo lỗi riêng bằng `problem_responses` (`app/core/openapi.py`). `/sandbox/*` nằm ngoài `/api/v1` nên không được ghi tự động.
2. `links` của Lab ở 6.3 hiển thị cả `cases`; hiện mã chỉ trả `self`, vì chỉ liệt kê tài nguyên đã có thật. Thêm `scenarios`/`cases`/`experiments` khi endpoint tương ứng tồn tại.

Cách chạy để xem trực tiếp: `alembic upgrade head`, `python -m app.seed`, `uvicorn app.main:app`, mở `http://127.0.0.1:8000/docs`, đăng nhập rồi bấm *Authorize* và dán `access_token` (không gõ `Bearer`). Backend dừng ngay lúc khởi động nếu CSDL chưa migrate.

---

## 1. Vì sao thiết kế lại

Bản v0.1 dùng đường dẫn kiểu **động từ** (`/deepfake/analyze`, `/prompt/run`, `/api-lab/run`), mỗi Lab một kiểu, và `GET /api/labs` trả mảng trần. Điều này vi phạm các nguyên tắc REST cơ bản. Bản này sửa theo bảng sau.

| Vấn đề ở v0.1 | Nguyên tắc REST | Cách sửa |
|---|---|---|
| `POST /deepfake/analyze`, `/prompt/run`, `/api-lab/run` là động từ | Đường dẫn là **danh từ**, hành động nằm ở phương thức HTTP | "Chạy Lab" = **tạo tài nguyên `experiment`**: `POST /labs/{lab}/experiments` |
| `/deepfake/upload` | Upload là tạo tài nguyên | `POST /media-assets` |
| `/experiments/{id}/report` dùng cho cả JSON và Markdown | Một URL, nhiều biểu diễn → **content negotiation** | Header `Accept` |
| Lỗi tự đặt `{error:{...}}` | Dùng chuẩn có sẵn | **RFC 9457** `application/problem+json` |
| Dữ liệu sai trả 400 | 400 = yêu cầu hỏng cú pháp; sai ngữ nghĩa = 422 | 400 / 422 tách bạch (khớp mặc định FastAPI) |
| `POST` khởi tạo không có `Location` | 201/202 phải trả `Location` | Bắt buộc `Location` |
| Danh sách trả mảng trần | Cần mở rộng (phân trang, tổng) mà không phá hợp đồng | Mọi danh sách trả đối tượng `{items, page, page_size, total}` |
| Không có phiên bản | API công khai cần phiên bản | Tiền tố `/api/v1` |
| `experiment_id` trong thân | Tên khóa nhất quán | `id` |
| Đánh giá một ca (`task_violated`) bằng endpoint riêng không rõ tài nguyên | Sửa một phần tài nguyên = `PATCH` | `PATCH /experiments/{id}/prompt-runs/{run_id}` |

**Quy ước từ v0.3 (Việt chốt 18/11):** danh từ cho thao tác CRUD, **động từ cho hành động không phải CRUD**. Vì vậy đăng nhập là `POST /auth/login` (không tạo tài nguyên nên trả `200`, không `Location`); hành động sau này (ví dụ hủy thử nghiệm) dùng `POST /experiments/{id}/cancel`. Các endpoint tạo tài nguyên thật (`experiments`, `media-assets`) vẫn dùng danh từ.

Các điểm **chưa nằm trong SP A.5.3**: SP chỉ liệt kê "API tối thiểu để phối hợp" dạng động từ; tiền tố `/api/v1` và đường dẫn mới là đề xuất của TV1 (xem D-13 đến D-15 ở mục 12).

---

## 2. Mô hình tài nguyên

```
/api/v1
├─ auth/login                       (POST: đăng nhập, nhận token; hành động, không phải CRUD)
├─ users/me                         (người dùng hiện tại)
├─ users                            (admin, C)
├─ health
├─ labs                             (danh mục Lab)
│   └─ {lab}                        deepfake | prompt_injection | api_lab | poisoning
│       ├─ scenarios                (deepfake, api_lab)
│       ├─ cases                    (prompt_injection)
│       └─ experiments   ← POST: tạo (chạy) một thử nghiệm
├─ experiments                      (lịch sử, đọc chung mọi Lab)
│   └─ {id}
│       ├─ events                   (nhật ký của thử nghiệm)
│       ├─ artifacts[/{name}]       (khung hình, tệp kết quả)
│       ├─ report                   (Markdown hoặc JSON, theo Accept)
│       ├─ verification             (Deepfake: lựa chọn xác minh)
│       └─ prompt-runs[/{run_id}]   (Prompt Injection: từng lần gọi mô hình)
├─ media-assets                     (video mẫu và video tải lên)
│   └─ {id}[/content]
└─ models[/{id}]                    (S: model được phê duyệt)

/sandbox/echo                       (NGOÀI /api/v1; đối tượng bị thử của Lab 03)
```

Quy ước đặt tên:
- Đường dẫn: danh từ **số nhiều**, chữ thường, **kebab-case** (`media-assets`, `prompt-runs`); riêng hành động không phải CRUD dùng động từ (`auth/login`, `.../cancel`).
- Trường JSON: **snake_case**.
- Định danh: chuỗi **mờ** có tiền tố (`usr_…`, `lab_…`, `exp_…`, `med_…` cho media-asset, `case_…`, `run_…`); client không suy luận cấu trúc bên trong.
- Lab được định danh trong đường dẫn bằng `code` (`prompt_injection`), không dùng số. Trường `id` số vẫn trả về để tương thích giao diện hiện tại (đánh dấu deprecated).
- Thời gian: ISO-8601 UTC, hậu tố `Z`.

---

## 3. Bảng endpoint

### 3.1. Xác thực và người dùng

| Phương thức & đường dẫn | Quyền | Ưu | Thành công | Ghi chú |
|---|---|---|---|---|
| `POST /auth/login` | công khai | M | `200` | Đăng nhập; trả token (6.4). Có giới hạn tần suất riêng (`LOGIN_RATE_LIMIT_PER_MINUTE`) |
| `GET /users/me` | user | M | `200` | Thông tin người dùng hiện tại |
| `GET /users` · `POST /users` · `PATCH /users/{id}` · `DELETE /users/{id}` | admin | C | `200/201/200/204` | Seed đủ cho MVP |

### 3.2. Hệ thống

| Phương thức & đường dẫn | Quyền | Ưu | Thành công |
|---|---|---|---|
| `GET /health` | công khai | M | `200` (DB, Ollama, FFmpeg, worker, phiên bản); `503` khi DB hoặc worker hỏng (6.8) |

### 3.3. Lab và danh mục kịch bản

| Phương thức & đường dẫn | Quyền | Ưu | Thành công |
|---|---|---|---|
| `GET /labs` | user | M | `200` |
| `GET /labs/{lab}` | user | M | `200` |
| `PATCH /labs/{lab}` | admin | S | `200` (đổi `status`/`enabled`) |
| `GET /labs/{lab}/scenarios` · `GET /labs/{lab}/scenarios/{code}` | user | M | `200` (Lab `deepfake`, `api_lab`) |
| `GET /labs/prompt_injection/cases` · `.../cases/{code}` | user | M | `200` |

### 3.4. Thử nghiệm (experiments)

| Phương thức & đường dẫn | Quyền | Ưu | Thành công | Ghi chú |
|---|---|---|---|---|
| `POST /labs/{lab}/experiments` | user | M | **`202`** + `Location` | Tạo và xếp hàng một thử nghiệm; thân khác nhau theo Lab (mục 5) |
| `GET /experiments` | user | M | `200` | Lọc, sắp xếp, phân trang (mục 7) |
| `GET /experiments/{id}` | user | M | `200` / `304` | Có `ETag` |
| `DELETE /experiments/{id}` | admin | C | `204` | Chỉ khi `done`/`failed`, nếu không `409` |
| `GET /experiments/{id}/events` | user | M | `200` | Phân trang; đã lọc dữ liệu nhạy cảm |
| `GET /experiments/{id}/artifacts` | user | S | `200` | Danh sách tệp kết quả |
| `GET /experiments/{id}/artifacts/{name}` | user | S | `200` | Tải một tệp (khung hình, âm thanh tách) |
| `GET /experiments/{id}/report` | user | M | `200` | `Accept: text/markdown` (mặc định) hoặc `application/json` |
| `PUT /experiments/{id}/verification` | user | S | `200` | Đặt/thay lựa chọn xác minh (idempotent) |
| `GET /experiments/{id}/verification` | user | S | `200` | — |
| `GET /experiments/{id}/prompt-runs` | user | M | `200` | Từng lần gọi mô hình |
| `PATCH /experiments/{id}/prompt-runs/{run_id}` | user | S | `200` | Chấm/ghi đè `task_violated` |

### 3.5. Tệp media

| Phương thức & đường dẫn | Quyền | Ưu | Thành công | Ghi chú |
|---|---|---|---|---|
| `POST /media-assets` | user | M | `201` + `Location` | `multipart/form-data`; mục 5.1 |
| `GET /media-assets` | user | M | `200` | Lọc `origin=sample|upload`, `label` |
| `GET /media-assets/{id}` | user | M | `200` | Siêu dữ liệu và nhãn |
| `GET /media-assets/{id}/content` | user | S | `200` | Tải/streaming nội dung |
| `DELETE /media-assets/{id}` | admin | C | `204` | Không xóa mẫu có sẵn (`origin=sample`) → `409` |

### 3.6. Quản trị

| Phương thức & đường dẫn | Quyền | Ưu | Thành công |
|---|---|---|---|
| (S, sau MVP) `GET /models` · `POST /models` · `PATCH /models/{id}` | admin | S | `200/201/200` — duyệt model bằng `PATCH {approved:true}`; chỉ cần khi có detector |

### 3.7. Sandbox (ngoài API chính)

| Phương thức & đường dẫn | Quyền | Ưu | Ghi chú |
|---|---|---|---|
| `GET /sandbox/echo` · `POST /sandbox/echo` | token Lab nội bộ (`X-Lab-Token`) | M | Đối tượng bị thử; trả `200` hoặc `429` kèm `Retry-After`. Không là tài nguyên nghiệp vụ nên không theo `/api/v1`. Chi tiết ở 6.9 |

---

## 4. Ngữ nghĩa phương thức và mã trạng thái

### 4.1. Phương thức

| Phương thức | An toàn | Idempotent | Dùng cho |
|---|---|---|---|
| `GET` | ✔ | ✔ | Đọc; **không bao giờ** thay đổi trạng thái |
| `POST` | ✘ | ✘ | Tạo tài nguyên (experiment, media-asset) hoặc hành động không phải CRUD (`auth/login`, sau này `experiments/{id}/cancel`) |
| `PUT` | ✘ | ✔ | Thay thế toàn bộ một tài nguyên con (`verification`) |
| `PATCH` | ✘ | ✘ | Sửa một phần (`prompt-runs/{run_id}`, `labs/{lab}`, `models/{id}`); thân là đối tượng chứa các trường cần đổi |
| `DELETE` | ✘ | ✔ | Xóa; trả `204`, gọi lại trả `404` |

Phương thức không hỗ trợ trên một đường dẫn → `405` kèm `Allow`.

### 4.2. Mã trạng thái thành công

| Mã | Khi nào |
|---|---|
| `200 OK` | Đọc hoặc cập nhật thành công, có thân; đăng nhập (`/auth/login`, không `Location`) |
| `201 Created` | Tạo đồng bộ xong một tài nguyên (`/media-assets`), **kèm `Location`** |
| `202 Accepted` | Đã nhận, xử lý bất đồng bộ (`POST /labs/{lab}/experiments`), **kèm `Location`** và `Retry-After` |
| `204 No Content` | Xóa thành công |
| `304 Not Modified` | `GET` có `If-None-Match` khớp `ETag` |

### 4.3. Mã lỗi (RFC 9457)

| HTTP | `code` | Khi nào |
|---|---|---|
| 400 | `MALFORMED_REQUEST` | JSON hỏng, thiếu thân, `multipart` sai |
| 401 | `UNAUTHENTICATED` | Thiếu/hết hạn/sai token (kèm `WWW-Authenticate: Bearer`); đăng nhập sai email/mật khẩu hoặc tài khoản bị khóa (cùng một thông báo) |
| 403 | `FORBIDDEN` | Sai vai trò |
| 404 | `NOT_FOUND` | Không có tài nguyên (cũng dùng khi người dùng không được thấy; `POST /labs/{lab}/experiments` với `{lab}` không tồn tại) |
| 405 | `METHOD_NOT_ALLOWED` | Phương thức không hỗ trợ (kèm `Allow`) |
| 406 | `NOT_ACCEPTABLE` | `Accept` không được hỗ trợ |
| 409 | `LAB_DISABLED` | Lab đang tắt (kiểm tra **trước** khi validate thân) |
| 409 | `STATE_CONFLICT` | Thao tác không hợp lệ ở trạng thái hiện tại (xóa experiment đang chạy) |
| 413 | `FILE_TOO_LARGE` | Vượt dung lượng |
| 415 | `UNSUPPORTED_MEDIA` | Loại tệp không được hỗ trợ (xác định theo nội dung, không theo đuôi) |
| 422 | `VALIDATION_ERROR` | Sai schema hoặc ngoài khoảng (`repeat > 5`, vượt trần số request/số lần gọi); lỗi từ `LabService.validate` cũng dùng mã này, `errors[].field` là tên trường trong thân. **Không** tạo experiment |
| 422 | `MISSING_CONSENT` | Mẫu thiếu `consent_ref` |
| 422 | `IDEMPOTENCY_KEY_REUSED` | `Idempotency-Key` còn hiệu lực nhưng đi với Lab hoặc thân yêu cầu khác (`errors[0].field = "Idempotency-Key"`). Khóa sai định dạng thì là `VALIDATION_ERROR` |
| 429 | `RATE_LIMITED` | Vượt giới hạn API điều khiển hoặc đăng nhập (kèm `Retry-After`, `RateLimit-*`). API điều khiển đếm **theo người dùng**, cửa sổ trượt 60 giây: mọi request đã xác thực tới `labs`, `experiments`, `media-assets` dùng chung hạn mức `API_RATE_LIMIT_PER_MINUTE` (120); `POST /labs/{lab}/experiments` và `POST /media-assets` còn bị hạn mức riêng `HEAVY_RATE_LIMIT_PER_MINUTE` (10). Request thiếu/sai token nhận `401` và không bị tính. `/health`, `/auth/login` và `/sandbox/echo` có bộ đếm riêng |
| 500 | `INTERNAL_ERROR` | Lỗi không lường; không lộ chi tiết nội bộ |
| 503 | `QUEUE_FULL` | Hàng đợi job đã đầy (`MAX_QUEUED_JOBS`); kèm `Retry-After`. Không tạo experiment; kiểm tra **sau** validate nên dữ liệu sai vẫn nhận `422` |
| 503 | `DEP_UNAVAILABLE` | Phụ thuộc cần thiết (Ollama, FFmpeg) chưa sẵn sàng khi tạo thử nghiệm, hoặc Lab chưa có service cài đặt (kèm `Retry-After` khi có thể thử lại) |

Lỗi **bên trong một job** (Ollama chết giữa chừng, timeout, kết quả bị từ chối) **không** là lỗi HTTP: experiment vẫn truy vấn được bằng `200`, có `state:"failed"` và `error.code` (mục 6.1).

Dạng lỗi:

```http
HTTP/1.1 422 Unprocessable Content
Content-Type: application/problem+json

{
  "type": "urn:lab:problem:validation-error",
  "title": "Dữ liệu không hợp lệ",
  "status": 422,
  "detail": "Trường 'repeat' phải nhỏ hơn hoặc bằng 5.",
  "instance": "/api/v1/labs/prompt_injection/experiments",
  "code": "VALIDATION_ERROR",
  "errors": [ { "field": "repeat", "message": "phải ≤ 5" } ]
}
```

`type`, `title`, `status`, `detail`, `instance` là các trường chuẩn của RFC 9457; `code` và `errors` là trường mở rộng của dự án. Frontend dựa vào `code`.

---

## 5. Tạo thử nghiệm: một mẫu, ba Lab

Mọi Lab dùng **cùng một mẫu**: `POST /labs/{lab}/experiments`. Thân yêu cầu khác nhau theo Lab và được kiểm tra bởi `LabService.validate(payload, settings)`. Khóa lạ trong thân bị từ chối (422). OpenAPI mô tả mỗi Lab một schema riêng.

### 5.1. Tiền đề của Lab Deepfake: tạo `media-asset`

```http
POST /api/v1/media-assets
Authorization: Bearer <jwt>
Content-Type: multipart/form-data; boundary=...

file=<video>; label=synthetic; source="..."; consent_ref="..."; technique="..."
```

```http
HTTP/1.1 201 Created
Location: /api/v1/media-assets/med_9b1e...
Content-Type: application/json

{
  "id": "med_9b1e...",
  "origin": "upload",
  "label": "synthetic",
  "source": "…", "consent_ref": "…", "technique": "…",
  "mime": "video/mp4", "size_bytes": 4821000, "sha256": "…",
  "duration_s": 12.4, "fps": 25, "width": 1280, "height": 720, "codec": "h264",
  "created_at": "2026-11-16T03:00:00Z"
}
```

Hành vi đã cài (T-13):

- Loại tệp xác định theo **nội dung** (magic bytes): MP4, MOV, WebM; đuôi và `Content-Type` do client gửi bị bỏ qua. Sai loại hoặc ffprobe không đọc được/không có luồng video → `415 UNSUPPORTED_MEDIA`.
- Dung lượng tối đa `MAX_UPLOAD_MB` (mặc định 50) → `413 FILE_TOO_LARGE` (kiểm cả `Content-Length` khai báo lẫn khi đọc luồng). Độ dài tối đa `MAX_VIDEO_SECONDS` (mặc định 60) → `422 VALIDATION_ERROR`.
- Thiếu hoặc rỗng `consent_ref` → `422 MISSING_CONSENT`; `label` phải là `original|synthetic`, `source` bắt buộc, văn bản tối đa 500 ký tự, thiếu `file` → `422 VALIDATION_ERROR`; không phải `multipart/form-data` → `400 MALFORMED_REQUEST`.
- Tệp lưu trong `storage/` với tên ngẫu nhiên (tên client không dùng); `GET .../content` trả `Content-Disposition: attachment; filename="<id>.<đuôi>"`.
- `GET /media-assets` lọc `origin`, `label`, phân trang; tham số lạ → `422`. Mọi người dùng đăng nhập xem được toàn bộ media.
- `DELETE` chỉ admin; `origin=sample` → `409 STATE_CONFLICT`. Xóa asset không xóa kết quả thử nghiệm cũ; thử nghiệm đang xếp hàng dùng asset đó sẽ thất bại.
- Giới hạn đã biết: Starlette nhận trọn phần multipart vào tệp tạm trước khi handler chạy, nên thân chunked không có `Content-Length` vẫn bị nhận hết trước khi bị từ chối `413`.

Mẫu có sẵn (`origin:"sample"`) được nạp từ `datasets/demo/MANIFEST.csv` khi seed; người dùng thường không xóa được.

### 5.2. Deepfake

```http
POST /api/v1/labs/deepfake/experiments
Content-Type: application/json

{ "asset_id": "med_9b1e...", "frame_limit": 24 }
```

`asset_id` dạng `med_…`; `frame_limit` là số nguyên 1…`MAX_FRAMES` (mặc định `MAX_FRAMES`).

### 5.3. Prompt Injection

```http
POST /api/v1/labs/prompt_injection/experiments
Content-Type: application/json

{ "case_codes": ["PI-01", "PI-02", "PI-03"], "defenses": [false, true], "repeat": 3 }
```

Một experiment chứa **cả hai nhánh** `defenses` để cùng điều kiện (D-07). `defenses` mặc định `[false, true]`, `repeat` mặc định 1 (tối đa 5). Tổng số lần gọi mô hình = số ca × số nhánh × `repeat` ≤ `MAX_PI_CALLS`, vượt → 422.

### 5.4. API Security

```http
POST /api/v1/labs/api_lab/experiments
Content-Type: application/json

{ "scenario_code": "burst-30", "rate_limit_modes": [false, true] }
```

`rate_limit_modes` là danh sách boolean (`false` = tắt, `true` = bật), mặc định `[false, true]`. Không có trường URL. Đích cố định là `/sandbox/echo`.

### 5.5. Phản hồi chung khi tạo

```http
HTTP/1.1 202 Accepted
Location: /api/v1/experiments/exp_3f2a9c...
Retry-After: 2
Content-Type: application/json

{
  "id": "exp_3f2a9c...",
  "lab": "prompt_injection",
  "state": "queued",
  "result_kind": null,
  "config": { "case_codes": ["PI-01","PI-02","PI-03"], "defenses": [false,true], "repeat": 3 },
  "created_by": "usr_1a2b...",
  "created_at": "2026-11-16T03:10:00Z",
  "started_at": null, "finished_at": null, "duration_ms": null,
  "result": null,
  "error": null,
  "links": {
    "self": "/api/v1/experiments/exp_3f2a9c...",
    "events": "/api/v1/experiments/exp_3f2a9c.../events",
    "report": "/api/v1/experiments/exp_3f2a9c.../report",
    "prompt_runs": "/api/v1/experiments/exp_3f2a9c.../prompt-runs"
  }
}
```

`config` là payload **đã chuẩn hóa** do `validate` trả về (có giá trị mặc định điền sẵn), không phải thân gốc client gửi.

Client lấy kết quả bằng cách **poll `Location`** theo gợi ý `Retry-After` cho đến khi `state ∈ {done, failed}`. `links` chỉ liệt kê tài nguyên con có thật cho Lab đó.

---

## 6. Biểu diễn tài nguyên

### 6.1. Experiment khi hoàn thành

```json
{
  "id": "exp_3f2a9c...",
  "lab": "prompt_injection",
  "state": "done",
  "result_kind": "real",
  "config": { "case_codes": ["PI-01"], "defenses": [false, true], "repeat": 3 },
  "created_by": "usr_1a2b...",
  "created_at": "2026-11-16T03:10:00Z",
  "started_at": "2026-11-16T03:10:01Z",
  "finished_at": "2026-11-16T03:10:48Z",
  "duration_ms": 47000,
  "result": {
    "summary": "…",
    "components": [ { "name": "ollama_chat", "kind": "real", "note": "mô hình …" } ],
    "metrics": {
      "baseline": { "runs": 3, "judged": 3, "task_violated": 2 },
      "defended": { "runs": 3, "judged": 3, "task_violated": 1 }
    },
    "artifacts": [],
    "limitations": ["Cỡ mẫu nhỏ; không khẳng định phòng thủ hiệu quả tuyệt đối."]
  },
  "error": null,
  "links": { "self": "…", "events": "…", "report": "…", "prompt_runs": "…" }
}
```

Khi `failed`: `result: null` và `"error": { "code": "JOB_TIMEOUT", "message": "…" }`. Không có **kết quả** một phần ở MVP: `result` luôn `null` và `result_kind` luôn `null` khi `failed` (kết quả bị `result_guard` từ chối không được lưu nội dung; lý do nằm ở `lab_results.guard_details` và event `result_rejected`). Riêng `DEP_UNAVAILABLE` (và `JOB_TIMEOUT` do service báo), các lượt gọi mô hình đã chạy thật vẫn được lưu dạng dữ liệu thô (`prompt_runs`); chúng không có nhãn, không thuộc tỉ lệ nào, và chỉ đọc được qua `prompt-runs` khi endpoint đó có (chưa làm). `error.message` là câu cố định, không chứa chi tiết mạng.

Mã lỗi của job (`error.code`), khác mã lỗi HTTP ở 4.3:

| `error.code` | Khi nào | Event cuối trong `/events` |
|---|---|---|
| `JOB_TIMEOUT` | Job chạy quá thời gian tối đa của Lab (`JOB_TIMEOUT_<LAB>`) | `timeout` |
| `RESULT_REJECTED` | `result_guard` từ chối kết quả của service (nhãn cao hơn trần Lab, thiếu `limitations`, khóa `accuracy`/`probability` tự đặt, `records` sai…) | `result_rejected` (`data.failed` liệt kê kiểm tra không đạt) |
| `INTERRUPTED` | Máy chủ khởi động lại khi job đang `running` | `interrupted` |
| `INTERNAL_ERROR` | Service ném ngoại lệ ngoài dự kiến (hoặc báo mã lỗi không được phép) hoặc lỗi ghi CSDL; chi tiết chỉ ở log máy chủ | `job_failed` |
| `DEP_UNAVAILABLE` | Phụ thuộc ngoài (Ollama trên Colab) mất kết nối giữa chừng, do service báo (FR-PI-10) | `dep_unavailable` (`data.partial_runs` = số lượt đã chạy được lưu) |

`result_kind` là mức **yếu nhất** của `components` và không mạnh hơn `status` của Lab (SPEC-01 mục 6). Frontend phải hiển thị cả `result_kind`, `components` và `limitations`.

### 6.2. Tập hợp (collection)

Mọi danh sách dùng cùng một dạng, để thêm trường sau này không phá hợp đồng:

```json
{
  "items": [ { "…": "…" } ],
  "page": 1,
  "page_size": 20,
  "total": 57
}
```

### 6.3. Lab

```json
{
  "id": 2,
  "code": "prompt_injection",
  "name": "Prompt Injection trên Chatbot",
  "status": "simulated",
  "enabled": true,
  "links": { "self": "/api/v1/labs/prompt_injection", "cases": "/api/v1/labs/prompt_injection/cases" }
}
```

`id` số được giữ để không phá `api.ts` hiện tại, đánh dấu **deprecated**; định danh chính thức là `code`. `status` ∈ `real | simulated | planned` là **trần** của `result_kind` (SPEC-01 mục 6). Bản đang chạy trả thêm `description` và chỉ có `links.self` (xem 6.7).

### 6.4. Đăng nhập

```http
POST /api/v1/auth/login
Content-Type: application/json

{ "email": "reviewer1@lab.local", "password": "******" }
```

```http
HTTP/1.1 200 OK
Cache-Control: no-store

{ "access_token": "eyJ...", "token_type": "Bearer", "expires_in": 3600 }
```

Email không phân biệt hoa/thường. Sai email, sai mật khẩu hoặc tài khoản bị khóa → cùng một `401` (không tiết lộ tài khoản có tồn tại hay không) và ghi log ứng dụng `login_failed` (không bảng riêng, D-09). Chưa có refresh token: chỉ có access token, thời hạn `JWT_EXPIRE_MINUTES` (mặc định 60); hết hạn thì đăng nhập lại. Gọi các endpoint khác bằng `Authorization: Bearer <access_token>`.

### 6.5. Chấm một lần chạy Prompt Injection

```http
PATCH /api/v1/experiments/exp_3f2a9c.../prompt-runs/run_77...
Content-Type: application/json

{ "task_violated": true }
```

```json
{
  "id": "run_77...",
  "case_code": "PI-01",
  "attempt": 2,
  "defense": false,
  "response": "…",
  "latency_ms": 2140,
  "model_name": "…",
  "task_violated": true,
  "judged_by": "human"
}
```

Quy tắc: `task_violated` mặc định `null`; service chỉ được đặt giá trị khi chấm bằng quy tắc (`judged_by="rule"`); PATCH đặt `judged_by="human"`; có thể đặt lại `null` để bỏ đánh giá.

### 6.6. Báo cáo (content negotiation)

```http
GET /api/v1/experiments/exp_3f2a9c.../report
Accept: text/markdown
```

→ `200`, `Content-Type: text/markdown; charset=utf-8`. Với `Accept: application/json` → bản cấu trúc. Loại khác → `406`.

### 6.7. Ví dụ phản hồi thật của các endpoint đã triển khai

Lấy từ bản chạy ngày 19/11/2026 (token rút gọn). Dùng làm dữ liệu mẫu cho client và mock của TV5.

`GET /api/v1/users/me` → `200`

```json
{
  "id": "usr_005e791f22034be2a1cc42f7a71560a9",
  "email": "admin@lab.local",
  "full_name": "Quản trị",
  "role": "admin",
  "created_at": "2026-11-19T01:30:35.653Z"
}
```

`GET /api/v1/labs?page_size=2` → `200` (`total` là tổng số Lab; thứ tự theo danh mục: deepfake, prompt_injection, api_lab, poisoning)

```json
{
  "items": [
    {
      "id": 1,
      "code": "deepfake",
      "name": "Deepfake & Identity Spoofing",
      "description": "Phân tích video để tìm tín hiệu bất thường (nghiên cứu, không kết luận tuyệt đối).",
      "status": "simulated",
      "enabled": true,
      "links": { "self": "/api/v1/labs/deepfake" }
    },
    {
      "id": 2,
      "code": "prompt_injection",
      "name": "Prompt Injection trên Chatbot",
      "description": "So sánh phản hồi mô hình trước/sau phòng thủ trong sandbox.",
      "status": "simulated",
      "enabled": true,
      "links": { "self": "/api/v1/labs/prompt_injection" }
    }
  ],
  "page": 1,
  "page_size": 2,
  "total": 4
}
```

`PATCH /api/v1/labs/poisoning` (admin) với `{ "enabled": false }` → `200`, trả Lab sau khi đổi. Thân chỉ nhận `status` và/hoặc `enabled`; khóa lạ hoặc thân rỗng → `422`.

Lỗi (đều `Content-Type: application/problem+json`; chú thích `//` chỉ để đọc, không có trong phản hồi):

```jsonc
// 401 — POST /api/v1/auth/login sai email/mật khẩu (kèm WWW-Authenticate: Bearer)
{ "type": "urn:lab:problem:unauthenticated", "title": "Chưa xác thực", "status": 401,
  "detail": "Email hoặc mật khẩu không đúng.", "instance": "/api/v1/auth/login", "code": "UNAUTHENTICATED" }

// 401 — thiếu hoặc hết hạn token
{ "type": "urn:lab:problem:unauthenticated", "title": "Chưa xác thực", "status": 401,
  "detail": "Thiếu, sai hoặc hết hạn token.", "instance": "/api/v1/labs", "code": "UNAUTHENTICATED" }

// 404 — GET /api/v1/labs/zzz
{ "type": "urn:lab:problem:not-found", "title": "Không tìm thấy", "status": 404,
  "detail": "Không tìm thấy Lab.", "instance": "/api/v1/labs/zzz", "code": "NOT_FOUND" }

// 422 — PATCH /api/v1/labs/api_lab với thân {}
{ "type": "urn:lab:problem:validation-error", "title": "Dữ liệu không hợp lệ", "status": 422,
  "detail": "Dữ liệu gửi lên không hợp lệ.", "instance": "/api/v1/labs/api_lab", "code": "VALIDATION_ERROR",
  "errors": [ { "field": "", "message": "Value error, Cần ít nhất một trường: status hoặc enabled" } ] }

// 400 — thân không phải JSON hợp lệ
{ "type": "urn:lab:problem:malformed-request", "title": "Yêu cầu không hợp lệ", "status": 400,
  "detail": "Nội dung yêu cầu không phải JSON hợp lệ.", "instance": "/api/v1/auth/login", "code": "MALFORMED_REQUEST" }
```

`405` có cùng dạng với `code: "METHOD_NOT_ALLOWED"` và header `Allow` (ví dụ `GET /api/v1/auth/login` → `Allow: POST`).

### 6.8. Sức khỏe hệ thống (`GET /api/v1/health`)

Công khai, không cần token, `Cache-Control: no-store`. Dùng để kiểm tra trước buổi demo (nhất là Ollama trên Colab).

```json
{
  "status": "degraded",
  "version": "1.0.0",
  "checked_at": "2026-11-20T14:05:31Z",
  "components": {
    "database": { "status": "up" },
    "worker":   { "status": "up", "queue_size": 0, "busy": false },
    "ffmpeg":   { "status": "up" },
    "ollama":   { "status": "down", "reason": "unreachable" }
  }
}
```

| Trường | Giá trị |
|---|---|
| `status` | `ok` (mọi thành phần `up`) · `degraded` (DB và worker chạy, còn thành phần khác không `up`) · `down` (DB hoặc worker hỏng) |
| `components.*.status` | `up` · `down` · `not_configured` (chỉ `ollama`, khi `OLLAMA_URL` để trống) |
| `components.ollama.reason` | Mã cố định: `not_configured`, `invalid_url` (không phải `https`/host ngoài `OLLAMA_ALLOWED_HOSTS`, không gọi mạng), `unreachable`, `unauthorized`, `bad_response`, `model_missing` (có `OLLAMA_MODEL` nhưng Colab không có mô hình đó) |
| Mã HTTP | `200` khi DB và worker chạy (kể cả `degraded`); `503` khi DB hoặc worker hỏng |

Quy tắc: Ollama được thăm dò bằng `GET {OLLAMA_URL}/api/tags` (timeout 2 s, không theo chuyển hướng) và kết quả được nhớ 30 s, nên Colab tắt không làm `/health` chậm hay lỗi. Phản hồi **không bao giờ** chứa địa chỉ đường hầm, token, đường dẫn tệp hay nội dung ngoại lệ. FFmpeg chỉ được tìm trong `PATH`.

### 6.9. Sandbox (`/sandbox/echo`)

Đối tượng bị thử của Lab API Security. **Không** dành cho frontend; chỉ Lab (worker) gọi tới `ctx.settings.sandbox_base_url` bằng `httpx`. Không dùng JWT.

| Header | Bắt buộc | Ý nghĩa |
|---|---|---|
| `X-Lab-Token` | có | Token Lab nội bộ (`ctx.settings.sandbox_token`). Thiếu/sai → `401 UNAUTHENTICATED` (kiểm tra trước khi đọc thân) |
| `X-Lab-Rate-Limit` | không | `on` (mặc định) hoặc `off`: bật/tắt giới hạn tần suất cho **riêng request này**. Giá trị khác → `422 VALIDATION_ERROR` (`errors[].field = "X-Lab-Rate-Limit"`) |
| `X-Lab-Run` | không | Khóa chạy, 1–64 ký tự `[A-Za-z0-9_-]` (nên dùng id experiment), mặc định `default`. Mỗi khóa có bộ đếm riêng để các lần chạy không ảnh hưởng nhau. Sai → `422` |

Bộ đếm tách theo chế độ: `on` dùng cửa sổ trượt `SANDBOX_MAX_REQUESTS`/`SANDBOX_WINDOW_SECONDS`; `off` chỉ đếm, không chặn. Bộ đếm này độc lập với giới hạn đăng nhập và API điều khiển.

`GET /sandbox/echo` → `200`

```json
{ "ok": true, "mode": "on", "count": 1, "method": "GET", "received_bytes": 0 }
```

Kèm `RateLimit-Limit` và `RateLimit-Remaining` khi `on`. `POST` nhận thân tối đa 4096 byte (lớn hơn → `413 PAYLOAD_TOO_LARGE`) và trả thêm `received_bytes` và `keys` (tên khóa cấp trên, tối đa 20) nếu thân là JSON object; **không echo giá trị** và không bao giờ gọi ra ngoài hay dùng nội dung thân làm địa chỉ/đường dẫn.

Vượt giới hạn khi `on` → `429` (`problem+json`, `code = RATE_LIMITED`) kèm `Retry-After`, `RateLimit-Limit`, `RateLimit-Remaining: 0`, `RateLimit-Reset`. Cùng một chuỗi request ở `off` luôn `200`, nên so sánh hai chế độ cho kết quả khác nhau.

---

## 7. Truy vấn danh sách

| Tham số | Ví dụ | Ý nghĩa |
|---|---|---|
| `page`, `page_size` | `?page=2&page_size=20` | Mặc định 1 và 20; `page_size` tối đa 100 |
| `lab` | `?lab=prompt_injection` | Lọc theo Lab |
| `state` | `?state=done` | Lọc trạng thái (có thể lặp: `state=done&state=failed`) |
| `result_kind` | `?result_kind=real` | Lọc nhãn trung thực |
| `created_by` | `?created_by=me` | Của tôi (`me`) hoặc id người dùng |
| `created_from`, `created_to` | ISO-8601 | Khoảng thời gian |
| `sort` | `?sort=-created_at` | Tiền tố `-` là giảm dần; mặc định `-created_at` |

Tham số không nhận ra → `422`. Mọi người dùng đã đăng nhập xem được thử nghiệm của cả nhóm; chỉ `admin` xóa.

---

## 8. Bất đồng bộ, đồng thời và lặp yêu cầu

- **Polling có điều kiện:** `GET /experiments/{id}` trả `ETag`; client gửi `If-None-Match` để nhận `304` khi chưa đổi, giảm tải máy một worker.
- **Hàng đợi:** chỉ một job `running` tại một thời điểm; các job khác ở `queued` theo thứ tự tạo. Khi khởi động lại máy chủ, job `running` thành `failed` (`INTERRUPTED`), job `queued` được xếp lại.
- **Chống trùng (S, đã có):** `POST /labs/{lab}/experiments` chấp nhận header `Idempotency-Key` (1–128 ký tự ASCII in được, không có khoảng trắng; sai định dạng → `422 VALIDATION_ERROR`). Khóa tính **theo người dùng**.
  - Cùng khóa, cùng Lab và cùng thân (so sánh theo nội dung JSON, không phụ thuộc thứ tự trường) trong 10 phút → `202` với **cùng** experiment thay vì tạo mới, `Location` như cũ và thêm `Idempotent-Replayed: true`. Thân là **trạng thái hiện tại** của experiment (có thể đã `done`), không phải ảnh chụp lúc tạo. Lần phát lại không kiểm lại Lab đang tắt, `validate` hay `QUEUE_FULL`, và không tính vào hàng đợi (nhưng vẫn tính vào hạn mức tần suất).
  - Cùng khóa nhưng khác Lab hoặc thân → `422 IDEMPOTENCY_KEY_REUSED`.
  - Yêu cầu bị từ chối (`422`, `409`, `503`) **không** giữ khóa: gửi lại với dữ liệu đúng vẫn tạo được. Khóa hết hạn sau 10 phút (tính từ lúc tạo experiment) và dùng lại được. Xóa experiment sẽ xóa khóa.
- **Cập nhật đồng thời (C):** `PATCH` có thể kèm `If-Match` với `ETag`; không khớp → `412 Precondition Failed`.
- **Trạng thái cuối:** `done` và `failed` không đổi nữa; poll dừng ở đó.

---

## 9. Tiêu đề HTTP

| Tiêu đề | Khi nào |
|---|---|
| `Authorization: Bearer <jwt>` | Mọi endpoint trừ `/auth/login`, `/health` |
| `Location` | `201`, `202` |
| `Idempotent-Replayed: true` | `202` của `POST /labs/{lab}/experiments` khi trả lại experiment cũ theo `Idempotency-Key` |
| `Retry-After` | `202` (gợi ý poll), `429`, `503` |
| `ETag` / `If-None-Match` | `GET /experiments/{id}`, `GET /media-assets/{id}` |
| `Allow` | `405` |
| `WWW-Authenticate: Bearer` | `401` |
| `Cache-Control: no-store` | Phản hồi chứa token hoặc dữ liệu cá nhân |
| `X-Content-Type-Options: nosniff` | Mọi phản hồi, đặc biệt khi tải tệp |
| `Content-Disposition` | Tải `artifacts/{name}`, `media-assets/{id}/content` (tên an toàn) |
| `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset` | API điều khiển và `/sandbox/echo` |

CORS: chỉ cho origin của frontend (`CORS_ORIGINS`), và **expose** `Location`, `Retry-After`, `ETag`, `RateLimit-*`, `Idempotent-Replayed` để trình duyệt đọc được.

---

## 10. Quyền truy cập

| Tài nguyên | `user` | `admin` |
|---|---|---|
| `labs`, `scenarios`, `cases` | đọc | đọc, `PATCH /labs/{lab}` |
| `experiments` và con | tạo, đọc; `PUT verification`; `PATCH prompt-runs` | + `DELETE` |
| `media-assets` | tạo, đọc | + `DELETE` |
| `models` (S, sau MVP), `users` | — | toàn quyền |

Kiểm tra quyền ở **tầng dependency** của FastAPI (`require_role`), không rải trong từng hàm nghiệp vụ. Khi người dùng không có quyền đọc một tài nguyên, trả `404` thay vì `403` nếu việc tiết lộ sự tồn tại là không mong muốn.

---

## 11. Ánh xạ từ v0.1 và tác động

### 11.1. Bảng đổi tên

| v0.1 | Hiện hành |
|---|---|
| `POST /api/auth/login` | `POST /api/v1/auth/login` (trả `200`) |
| `GET /api/auth/me` | `GET /api/v1/users/me` |
| `GET /api/health` | `GET /api/v1/health` |
| `GET /api/labs` (mảng) | `GET /api/v1/labs` (`{items,…}`) |
| `POST /api/deepfake/upload` | `POST /api/v1/media-assets` |
| `GET /api/deepfake/samples` | `GET /api/v1/media-assets?origin=sample` |
| `POST /api/deepfake/analyze` | `POST /api/v1/labs/deepfake/experiments` |
| `POST /api/experiments/{id}/verification` | `PUT /api/v1/experiments/{id}/verification` |
| `GET /api/prompt/cases` | `GET /api/v1/labs/prompt_injection/cases` |
| `POST /api/prompt/run` | `POST /api/v1/labs/prompt_injection/experiments` |
| `PATCH /api/prompt/runs/{id}` | `PATCH /api/v1/experiments/{id}/prompt-runs/{run_id}` |
| `POST /api/api-lab/run` | `POST /api/v1/labs/api_lab/experiments` |
| `GET /api/experiments…` | `GET /api/v1/experiments…` |
| lỗi `{error:{code,…}}`; 400 cho sai dữ liệu | `application/problem+json`; 422 cho sai dữ liệu |
| `experiment_id` | `id` |

### 11.2. TV5 phải đổi gì (frontend hiện tại)

1. `api.ts`: tiền tố `/api/v1`; đăng nhập bằng `POST /api/v1/auth/login` rồi gửi `Authorization: Bearer` cho mọi lời gọi khác; `getLabs()` đọc `response.items` thay vì mảng; kiểu `Lab` thêm `code`, `enabled`, `links`; `LabStatus = 'real' | 'simulated' | 'planned'`.
2. Dashboard: thẻ "Lab có trạng thái active" (`status === 'active'`) phải đổi nghĩa (ví dụ đếm Lab `real`/`simulated`).
3. Luồng chạy Lab: `POST` → đọc `Location` → poll → dừng ở `done|failed`; hiển thị `result_kind`, `components`, `limitations`.
4. Xử lý lỗi theo `problem+json` (`code`, `errors[]`). Token hết hạn (401) thì chuyển về màn đăng nhập.
5. Vite proxy `/api` giữ nguyên (đã khớp tiền tố mới); thêm `/sandbox` **không** cần vì chỉ backend gọi.

### 11.3. TV2, TV3, TV4 phải đổi gì

Hợp đồng `LabService` **chốt theo SPEC-01 v0.5 mục 9.1** (bản cài đặt ở `backend/app/services/base.py`; hướng dẫn từng bước ở `docs/integration.md`): `validate(payload, settings)` và `run(ctx, payload)`. So với bản nháp SPEC v0.3, thay đổi là `validate` nhận thêm `settings` (để đọc các trần), `LabResult`/`Component` là dataclass, `ctx` có `emit()` và `check()`, và `records["prompt_runs"]` dùng `case_code`. REST chỉ thay lớp `api/`; mỗi người vẫn chỉ cài `validate` và `run`. Tên khóa trong `payload` theo mục 5 (`asset_id`, `case_codes`, `scenario_code`…); `rate_limit_modes` và `defenses` là danh sách boolean.

### 11.4. Tệp router trong repo

Repo có các tệp rỗng `api/deepfake.py`, `api/prompt.py`, `api/api_lab.py` (cũ) và `api/v1/` đã có `auth.py`, `labs.py`, `router.py`. Với REST theo tài nguyên đề xuất cấu trúc:

```
backend/app/api/v1/
├─ router.py          # gom các router, prefix /api/v1
├─ auth.py            # /auth/login, /users/me, /users
├─ health.py
├─ labs.py            # /labs, /labs/{lab}, scenarios, cases, POST experiments
├─ experiments.py     # /experiments và các tài nguyên con
├─ media_assets.py
└─ models.py          # (S)
backend/app/api/sandbox.py   # /sandbox/echo, ngoài /api/v1
backend/app/models/schemas/  # lab_deepfake.py, lab_prompt.py, lab_api.py: schema thân POST theo Lab
```

Ba tệp `deepfake.py`, `prompt.py`, `api_lab.py` cũ không còn là router; nếu muốn giữ chỗ cho từng người, chuyển thành **schema thân yêu cầu** trong `models/schemas/`.

---

## 12. Quyết định cần nhóm xác nhận

| ID | Quyết định **[ĐỀ XUẤT]** | Lý do | Nếu đổi |
|---|---|---|---|
| D-13 | Tiền tố `/api/v1` | Có phiên bản; thay đổi sau này không phá client cũ | Giữ `/api`: ít sửa cho TV5 nhưng không có đường nâng cấp |
| D-14 | Tạo thử nghiệm bằng `POST /labs/{lab}/experiments`, đọc bằng `/experiments` | Danh từ; schema riêng theo Lab; URL đọc thống nhất | `POST /experiments` với `lab` trong thân: một endpoint nhưng schema trộn lẫn |
| D-15 | Lỗi theo RFC 9457; sai dữ liệu = 422 | Chuẩn mở; khớp mặc định FastAPI | Giữ envelope tự đặt: tự bảo trì, mất công cụ chung |
| D-16 | Mọi danh sách là `{items,page,page_size,total}` | Mở rộng không phá hợp đồng | Mảng trần: gọn hơn nhưng mỗi lần thêm phân trang là thay đổi phá vỡ |
| D-17 | Định danh Lab trong đường dẫn là `code`, giữ `id` số deprecated | Dễ đọc, ổn định, tương thích tạm thời | Dùng `id` số: khó đọc, lộ thứ tự |
| D-19 | Danh từ cho CRUD, **động từ cho hành động không phải CRUD**: `POST /auth/login` (200), sau này `POST /experiments/{id}/cancel` | Đăng nhập không tạo tài nguyên nên không hợp với `201 + Location`; cách gọi quen thuộc với client | `POST /auth/tokens` (201): thuần danh từ nhưng gượng với client |
| D-20 | Chưa có refresh token ở MVP; chỉ access token `JWT_EXPIRE_MINUTES` | Cần thêm bảng `refresh_tokens`, `/auth/refresh`, `/auth/logout`, frontend tự làm mới; chưa cần cho demo một máy | Muốn phiên dài: tăng `JWT_EXPIRE_MINUTES`, hoặc làm refresh ở bản sau |

---

## 13. Việc triển khai (bổ sung cho backlog SPEC-01)

| ID | Việc | Thay cho | Tuần |
|---|---|---|---|
| T-06 | `GET /api/v1/labs`, `GET /labs/{lab}` đọc từ DB; báo TV5 (**xong phía backend**) | T-06 cũ | 1 |
| T-08 | Orchestrator, hàng đợi, vòng đời (**xong**); `POST /labs/{lab}/experiments` → 202 + `Location` và `GET /experiments/{id}` với `ETag` thuộc T-09 | T-08 cũ | 2 |
| T-09 | `POST /labs/{lab}/experiments`, `GET /experiments` (lọc, sắp xếp, phân trang), `/{id}`, `/events`, `/report` (Accept) (**xong**, 31 test hợp đồng) | T-09 cũ | 2 |
| T-11 | Xuất `docs/api-contract.md` từ tài liệu này; bật OpenAPI với `tags`, `operation_id` ổn định (**xong** cho endpoint đã có) | T-11 cũ | 1 |
| T-16 | Bộ xử lý lỗi toàn cục trả `problem+json`; ánh xạ 400/422; `405` kèm `Allow` (**xong**) | mới | 1 |
| T-17 | Test hợp đồng: mỗi endpoint kiểm tra mã, `Location`, `Content-Type`, dạng lỗi | mới | 2 |

Tiêu chí hoàn thành của hợp đồng: `/openapi.json` sinh ra khớp tài liệu này; mỗi endpoint M có ít nhất một ca thành công và một ca lỗi trong test.

---

## Phụ lục. Lịch sử phiên bản

| Phiên bản | Ngày | Nội dung |
|---|---|---|
| 0.2 | 15/11/2026 | Bản REST theo tài nguyên đầu tiên |
| 0.3 | 18/11/2026 | Đăng nhập là `POST /auth/login` (200, không `Location`); thêm D-19, D-20; định danh media-asset là `med_`; chốt chữ ký `LabService` (11.3); ghi rõ mặc định/ràng buộc thân yêu cầu từng Lab (mục 5); `rate_limit_modes` là boolean; mô tả 401 đăng nhập và giới hạn tần suất đăng nhập |
| 0.4 | 19/11/2026 | Công bố trong repo (T-11): thêm mục 0 (trạng thái triển khai, đối chiếu `/openapi.json`) và 6.7 (ví dụ thật); mã lỗi job (`JOB_TIMEOUT`, `RESULT_REJECTED`, `INTERRUPTED`, `INTERNAL_ERROR`) ở 6.1; ghi rõ `config` là payload đã chuẩn hóa (5.5), `LAB_DISABLED` kiểm tra trước validate, 404 khi `{lab}` không tồn tại, 503 khi Lab chưa có service (4.3); hàng đợi và khởi động lại (mục 8) |
| 0.5 | 19/11/2026 | T-09: nối router experiments (tạo, danh sách, chi tiết + ETag, events, report theo `Accept`); bổ sung hành vi cụ thể ở mục 0 (report `409` khi chưa kết thúc, thiếu thân → `400`) |
| 0.6 | 20/11/2026 | `error.code = DEP_UNAVAILABLE` (event `dep_unavailable`) khi phụ thuộc ngoài mất kết nối giữa chừng (FR-PI-10); job `failed` vẫn có `result`/`result_kind` = `null` nhưng các lượt `prompt_runs` đã chạy được lưu; `error.message` là câu cố định (6.1) |
| 0.7 | 20/11/2026 | T-10: thêm `GET /api/v1/health` (6.8): `ok`/`degraded`/`down`, mã lý do Ollama cố định, `503` chỉ khi DB hoặc worker hỏng |
| 0.8 | 21/11/2026 | T-12: thêm `/sandbox/echo` (6.9): `X-Lab-Token`, `X-Lab-Rate-Limit: on\|off` theo từng request, `X-Lab-Run`, bộ đếm tách theo chế độ, `413` cho thân > 4096 byte |
| 0.9 | 21/11/2026 | T-13: `/media-assets` (tạo multipart, danh sách, chi tiết + ETag, `content`, `DELETE`); `FILE_TOO_LARGE`, `UNSUPPORTED_MEDIA`, `MISSING_CONSENT` đã cài (5.1) |
| 0.10 | 21/11/2026 | Giới hạn tần suất API điều khiển theo người dùng (`control`, `heavy`; 429 + `Retry-After` + `RateLimit-*`); trần hàng đợi `MAX_QUEUED_JOBS` → `503 QUEUE_FULL`. Giới hạn đã biết: bộ đếm trong bộ nhớ (1 tiến trình); thân `multipart` của `POST /media-assets` được nhận xong trước khi kiểm hạn mức |
| 0.11 | 22/11/2026 | `Idempotency-Key` cho `POST /labs/{lab}/experiments` (mục 8): phát lại trả cùng experiment + `Idempotent-Replayed`, khác Lab/thân → `422 IDEMPOTENCY_KEY_REUSED`; bảng `idempotency_keys` (migration 0003) |
| 0.12 | 22/11/2026 | OpenAPI: mọi lỗi 4xx/5xx của `/api/v1` mô tả bằng `application/problem+json` (`ProblemDetails`), bỏ `HTTPValidationError`; không đổi hành vi API |
| 0.13 | 23/11/2026 | Tích hợp TV4: `GET`/`PATCH /experiments/{id}/prompt-runs` đã cài (6.5); `links.prompt_runs` cho experiment `prompt_injection`; `metrics` của `GET /experiments/{id}` và `/report` tính lại từ `prompt_runs` (ASR `null` đến khi mọi lượt tấn công của nhánh có kết luận); `scenario_code` của `api_lab` là `burst-N`; mã ca Prompt Injection `PI-01…PI-14` |
