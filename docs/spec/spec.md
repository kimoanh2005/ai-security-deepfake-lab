# ĐẶC TẢ KIẾN TRÚC & BACKEND — AI Security & Deepfake Lab

| | |
|---|---|
| **Tài liệu** | SPEC-01 · Đặc tả yêu cầu và kiến trúc backend (hợp đồng API chi tiết: API-01) |
| **Phiên bản** | 0.8 (bản nháp để nhóm xác nhận; chốt `/sandbox/echo`, FR-API-01) |
| **Ngày** | 19/11/2026 |
| **Người phụ trách** | TV1 (Trưởng nhóm, kiến trúc & backend) |
| **Căn cứ** | Bản đề xuất sản phẩm (SP); báo cáo "AI Security và Deepfake" (1.3.2, 3.2.1–3.2.3); thiết kế backend v0 (Phần B); quét repo ngày 15/11/2026 |
| **Trạng thái** | Các mục đánh dấu **[ĐỀ XUẤT]** là quyết định mặc định của TV1, chưa được nhóm xác nhận. Các mục **[MỞ]** cần nhóm quyết. |

Quy ước tham chiếu: `API-01` = tài liệu *Hợp đồng REST API v0.8*; mọi đường dẫn dưới đây tính từ tiền tố `/api/v1`. `SP A.x` = mục x của bản đề xuất sản phẩm. Mức ưu tiên: **M** (Must, bắt buộc cho MVP), **S** (Should), **C** (Could).

---

## 1. Phân tích hiện trạng và mục tiêu

### 1.1. Hiện trạng repo (quét 15/11/2026, nhánh `main`, 1 commit)

| Thành phần | Hiện có | Khoảng cách so với SP |
|---|---|---|
| Backend | `backend/main.py` (24 dòng): `GET /`, `GET /api/labs` (dữ liệu cố định, `status:"active"` cho Lab 1–3) | Chưa có xác thực, CSDL, vòng đời thử nghiệm, upload, log, test, migration |
| Backend (cấu trúc) | `api/`, `models/`, `services/` có tên tệp nhưng **0 dòng** (`deepfake.py`, `prompt.py`, `api_lab.py`, `database.py`, `video_service.py`, `ollama_service.py`, `security_service.py`) | `main.py` nằm ngoài `backend/app/` (SP A.5.2) |
| Cấu hình | CORS `allow_origins=["*"]` + `allow_credentials=True`; `requirements.txt` có `sqlalchemy`, `opencv`, `scikit-learn`; thiếu Alembic, JWT, băm mật khẩu, httpx | Chưa có `.env.example`, `scripts/`, `datasets/`, `tests/` |
| Frontend | Dashboard, danh sách Lab, trang thông tin; `api.ts` kiểm tra dữ liệu; đếm Lab "active" bằng `status === 'active'` | Phụ thuộc hợp đồng `GET /api/labs` hiện tại (xem 8.3) |
| TV2, TV3, TV4 | Không có mã | — |

### 1.2. Phân tích: vấn đề kiến trúc cần giải quyết

Từ SP và hiện trạng, có sáu vấn đề quyết định hình dạng kiến trúc:

| # | Vấn đề | Hệ quả nếu không xử lý | Hướng giải quyết (mục) |
|---|---|---|---|
| P1 | Ba người (TV2, TV3, TV4) viết module AI độc lập, mỗi người một kiểu kết quả | Khó tích hợp, dashboard không thống nhất | Hợp đồng `LabService` + `LabResult` chung (5) |
| P2 | SP bắt buộc tách **thật / mô phỏng / dự kiến** | Dễ "tô" kết quả; vi phạm nguyên tắc trung thực | `result_kind` là ràng buộc kỹ thuật (6, 7) |
| P3 | Deepfake và Ollama chạy lâu | HTTP đồng bộ timeout, UI đứng | Job bất đồng bộ + polling (5.3) |
| P4 | Rate Limiting Lab có thể khóa chính dashboard | Không bật/tắt được để so sánh | Endpoint sandbox tách biệt (9.4) |
| P5 | Các Lab so sánh "trước/sau" cùng bộ ca | Hai lần chạy rời nhau có thể khác điều kiện | Một experiment chứa cả hai nhánh (9.3, 9.4) |
| P6 | Một người làm backend cho cả nhóm trong 6 tuần | Quá tải, tích hợp muộn | Chia giai đoạn MVP; stub từ tuần 1 (11, 12) |

### 1.3. Mục tiêu kiến trúc

1. **Làm song song được từ tuần 1:** hợp đồng API và `LabService` khóa sớm, mỗi người thay stub bằng bản thật.
2. **Trung thực theo thiết kế:** hệ thống không thể lưu một kết quả thiếu nhãn, thiếu giới hạn, hoặc nhãn cao hơn mức Lab được phép.
3. **Chạy được trên một máy Windows, không dịch vụ trả phí** (SP A.8).
4. **Đủ đơn giản cho 6 tuần:** một tiến trình, một worker, SQLite.
5. **An toàn theo ba cổng** của báo cáo (1.3.2): đầu vào – xử lý – đầu ra.

### 1.4. Phạm vi của SPEC này

**Trong phạm vi:** backend (API, điều phối, CSDL, xác thực, log, báo cáo), hợp đồng với module Lab và với frontend, cấu trúc repo, tiêu chí nghiệm thu phần backend.
**Ngoài phạm vi:** thuật toán phát hiện Deepfake (TV3), tiền xử lý video chi tiết (TV2), nội dung ca Prompt Injection và chatbot (TV4), giao diện (TV5). Các phần này chỉ được mô tả ở mức **giao diện với backend**.

---

## 2. Quyết định mặc định và giả định

Các câu hỏi mở ở phần phân tích hướng đi trước đó được TV1 đưa ra mặc định để có thể bắt đầu. Nhóm có thể đổi; mỗi quyết định ghi rõ ảnh hưởng.

| ID | Quyết định **[ĐỀ XUẤT]** | Lý do | Nếu đổi thì ảnh hưởng |
|---|---|---|---|
| D-01 | **Hướng 2 (cân bằng):** dùng `LabService` + vòng đời experiment + `result_kind`; hoãn RBAC chi tiết và hash-model đến khi có detector | Vừa tích hợp ổn vừa kịp 6 tuần | Chọn hướng 3: thêm 2 vai trò, `model_registry` từ đầu (+~3–4 ngày) |
| D-02 | **Lab 04 Data Poisoning ra khỏi MVP**, giữ `status=planned` | SP gọi là "nâng cao, nếu kịp"; chưa có người phụ trách | Nếu giao cho ai đó: thêm FR-PO-* |
| D-03 | **Âm thanh:** MVP chỉ xử lý hình; khi video có âm thanh thì tách và lưu tệp, **không phân tích**, ghi vào `limitations` | SP A.4.1 chỉ đòi hỏi "tách âm thanh (nếu có)" | Phân tích âm thanh cần TV3 thêm mô hình riêng |
| D-04 | **Hàng đợi job:** một worker thread trong tiến trình FastAPI, xử lý tuần tự | Một máy, SQLite, tránh Celery/Redis trên Windows | Nhiều worker cần đổi cơ chế khóa ghi |
| D-05 | **Vai trò MVP: 2** (`admin`, `user`); cột `role` vẫn có để mở rộng `reviewer` | SP A.12: MVP chưa có detector nên chưa có điểm số để che | Có detector → thêm `reviewer`, che `suspicion_score` cho `user` |
| D-06 | **Đăng nhập có, đăng ký không:** tài khoản tạo bằng `seed` | Demo một máy; SP A.3 chỉ yêu cầu "xác thực cơ bản" | Đăng ký/quên mật khẩu = ngoài phạm vi |
| D-07 | **So sánh trước/sau trong cùng một experiment** (hai nhánh `defense=false/true`) | Bảo đảm cùng bộ ca và điều kiện (P5) | Chạy rời nhau cần thêm API so sánh |
| D-08 | **Chấm `task_violated`:** quy tắc tự động cho ca có tiêu chí rõ; người xác nhận/ghi đè; mặc định `NULL` | Tránh tự gán thành công (SP A.4.2) | Chỉ quy tắc: nhanh nhưng dễ sai ngữ nghĩa |
| D-09 | **Ranh giới TV1 ↔ TV4:** TV1 sở hữu khung `/sandbox` và cơ chế `experiment_events`; TV4 sở hữu kịch bản, số liệu, đánh giá. **Không có bảng `security_events`:** `rate_limited` ghi vào `experiment_events` của lần chạy; `login_failed`, `file_rejected` ghi log ứng dụng (logging), không lưu mật khẩu/token | Sự kiện bảo mật gắn với một lần chạy thì đã nằm trong `experiment_events`; phần còn lại là log vận hành, không cần truy vấn bằng SQL ở MVP | Cần truy vết bảo mật lâu dài/tìm kiếm: thêm bảng `security_events` ở bản sau |
| D-10 | **`result_kind` của experiment = mức yếu nhất trong các thành phần** (xem 6.3) | Thận trọng, tránh overclaim | Dùng mức "tiêu đề" sẽ gọn hơn nhưng dễ gây hiểu nhầm |
| D-11 | **Báo cáo xuất:** Markdown ở MVP; PDF là `C` | SP A.4.5 chỉ nói "bản tóm tắt" | — |
| D-12 | **Dịch vụ không truy cập CSDL trực tiếp**: `LabResult` có `records` để orchestrator ghi vào bảng nghiệp vụ | Service kiểm thử không cần DB; một chỗ ghi duy nhất | Cho service ghi DB: khó kiểm soát nhãn và giao dịch |
| D-13 | **Tiền tố `/api/v1`** | Có phiên bản, nâng cấp không phá client cũ | Giữ `/api`: ít sửa cho TV5 nhưng không có đường nâng cấp |
| D-14 | **Tạo thử nghiệm bằng `POST /labs/{lab}/experiments`**, đọc bằng `/experiments` | Danh từ, schema riêng theo Lab | `POST /experiments` với `lab` trong thân: gọn nhưng schema trộn lẫn |
| D-15 | **Lỗi theo RFC 9457; sai dữ liệu = 422** | Chuẩn mở, khớp mặc định FastAPI | Envelope tự đặt: tự bảo trì |
| D-16 | **Mọi danh sách là `{items,page,page_size,total}`** | Mở rộng không phá hợp đồng | Mảng trần: mỗi lần thêm phân trang là thay đổi phá vỡ |
| D-17 | **Định danh Lab trong đường dẫn là `code`**, giữ `id` số tạm thời | Dễ đọc, ổn định, tương thích giao diện hiện tại | Dùng `id` số: khó đọc |
| D-18 | **Ollama chạy trên Google Colab (GPU), không chạy trên máy cục bộ.** Backend gọi qua đường hầm HTTPS tới **một** địa chỉ cố định trong `.env` (`OLLAMA_URL`) kèm token (`OLLAMA_API_KEY`); địa chỉ chỉ được kiểm tra theo danh sách cho phép (`OLLAMA_ALLOWED_HOSTS`); client không bao giờ cung cấp địa chỉ | Máy demo của nhóm không có GPU/đủ RAM cho mô hình; TV4 đã chọn Colab | Quay lại cục bộ: chỉ đổi `.env`, không đổi mã. Dùng API LLM trả phí: đổi client và nguyên tắc dữ liệu rời khỏi máy |

---

## 3. Tác nhân và ca sử dụng

### 3.1. Tác nhân

| Tác nhân | Mô tả | Quyền (MVP) |
|---|---|---|
| **User** | Giảng viên, sinh viên chạy thử Lab | Chạy Lab, xem lịch sử của mình và của nhóm, xuất báo cáo |
| **Admin** | TV1 / người quản trị demo | Như User + quản lý tài khoản, bật/tắt Lab, xem thử nghiệm của mọi người dùng |
| **Lab module** | Code của TV2/TV3/TV4 | Chỉ qua `LabService`; không tự mở kết nối ra ngoài. Ngoại lệ duy nhất: Lab Prompt Injection gọi **một** đích LLM đã cấu hình trong `.env` (xem D-18, FR-PI-04) |
| **Ollama (Colab)** | Dịch vụ LLM chạy trên Google Colab, mở ra qua đường hầm HTTPS có token. Là hệ thống bên ngoài, **không tin cậy**: phản hồi coi như dữ liệu, có thể chậm/mất kết nối | Chỉ nhận yêu cầu do **backend** gọi từ Lab Prompt Injection; trình duyệt không bao giờ gọi trực tiếp |
| **Người vận hành Colab** | TV4 (hoặc người được TV4 chỉ định) | Bật notebook Colab, nạp mô hình, mở đường hầm; chuyển cho TV1 địa chỉ + token qua kênh riêng (không commit); tắt khi xong buổi demo |

### 3.2. Ca sử dụng

| UC | Tên | Tác nhân | Tóm tắt |
|---|---|---|---|
| UC-01 | Đăng nhập | User/Admin | Nhập tài khoản → nhận token |
| UC-02 | Xem danh sách Lab và trạng thái tích hợp | User | Mỗi Lab có `status` (real/simulated/planned) |
| UC-03 | Tải video mẫu và phân tích | User | Upload → analyze → xem khung hình, siêu dữ liệu, cảnh báo, (điểm nếu có detector) |
| UC-04 | Chạy Prompt Injection trước/sau phòng thủ | User | Chọn ca → chạy hai nhánh → xem so sánh, ca thất bại |
| UC-05 | Chạy thử API Security | User | Chọn kịch bản → chạy có/không rate limit → xem 200/429, độ trễ |
| UC-06 | Xem lịch sử, lọc, mở chi tiết | User | Lọc theo Lab, loại kết quả, trạng thái |
| UC-07 | Xuất báo cáo ngắn | User | Mục đích, điều kiện, kết quả, giới hạn |
| UC-08 | Chấm/ghi đè đánh giá ca Prompt Injection | User/Admin | Cập nhật `task_violated`, ghi `judged_by=human` |
| UC-09 | Quản trị: tài khoản, bật/tắt Lab, xem nhật ký bảo mật | Admin | — |

---

## 4. Yêu cầu chức năng

Mỗi yêu cầu có tiêu chí chấp nhận (**AC**) kiểm chứng được. ID dùng cho test và PR.

### 4.1. Xác thực và phân quyền (AUTH)

| ID | Yêu cầu | Ưu | Nguồn | AC |
|---|---|---|---|---|
| FR-AUTH-01 | Đăng nhập bằng email/password (email lưu chữ thường, không phân biệt hoa/thường khi đăng nhập), trả JWT có thời hạn cấu hình được | M | SP A.5 | Sai mật khẩu → 401 + ghi log ứng dụng `login_failed` (không ghi mật khẩu); đúng → token |
| FR-AUTH-02 | Mật khẩu lưu dạng băm có salt; không bao giờ trả về trong API | M | SP A.8 | Kiểm tra DB và phản hồi không chứa mật khẩu thô |
| FR-AUTH-03 | Mọi endpoint `/api/v1/*` (trừ `POST /auth/login`, `GET /health`) yêu cầu token hợp lệ | M | SP A.5.1 | Không token → 401 |
| FR-AUTH-04 | Endpoint quản trị chỉ `admin` | M | D-05 | `user` gọi → 403 |
| FR-AUTH-05 | Tài khoản mẫu tạo bằng `scripts/seed.py`; không có đăng ký công khai | M | D-06 | Chạy seed 2 lần không tạo trùng |

### 4.2. Danh mục Lab (LAB)

| ID | Yêu cầu | Ưu | Nguồn | AC |
|---|---|---|---|---|
| FR-LAB-01 | `GET /labs` trả danh sách Lab (dạng `{items,…}`) với `status` ∈ {real, simulated, planned}, `enabled` | M | SP A.4.5 | Dashboard hiển thị đúng nhãn |
| FR-LAB-02 | `status` của Lab là **trần** của `result_kind` mọi experiment thuộc Lab đó | M | SP A.0 | Xem 6.2 |
| FR-LAB-03 | Chỉ `admin` đổi `status`/`enabled`; mỗi lần đổi ghi log ứng dụng (ai, lab, giá trị mới) | S | — | — |
| FR-LAB-04 | Lab `enabled=false` → mọi yêu cầu chạy trả 409 `LAB_DISABLED` | S | — | — |

### 4.3. Vòng đời thử nghiệm (EXP)

| ID | Yêu cầu | Ưu | Nguồn | AC |
|---|---|---|---|---|
| FR-EXP-01 | Mọi lần chạy Lab tạo một **experiment** với máy trạng thái `queued → running → done/failed` | M | SP A.4.5, A.8 | Kiểm tra chuyển trạng thái hợp lệ (5.2) |
| FR-EXP-02 | `POST /labs/{lab}/experiments` trả ngay `202` + `Location` + biểu diễn experiment (`state:queued`); kết quả lấy bằng `GET /experiments/{id}` | M | SP A.5.3 | Không chặn HTTP quá thời gian cấu hình |
| FR-EXP-03 | Chỉ một job `running` tại một thời điểm; job khác chờ `queued` | M | D-04 | Hai yêu cầu đồng thời không gây lỗi khóa DB |
| FR-EXP-04 | Khi khởi động, mọi job `running` cũ đổi thành `failed` (`error.code=INTERRUPTED`, event `interrupted`); job `queued` cũ **được xếp hàng lại** (chưa chạy nên không mất) | M | — | Khởi động lại giữa lúc chạy → không còn job treo; job đang chờ vẫn chạy |
| FR-EXP-05 | Mỗi job có thời gian tối đa (timeout theo Lab); quá hạn → `failed` (event `timeout`) | M | SP A.11 | — |
| FR-EXP-06 | Kết quả `done` **bắt buộc** hợp lệ theo 6.1: có `result_kind` ≤ trần Lab, `limitations` không rỗng, không có khóa cấm | M | SP A.0, A.8 | Service trả kết quả sai → experiment `failed`, event `result_rejected` |
| FR-EXP-07 | `GET /experiments` hỗ trợ lọc `lab`, `result_kind`, `state`, `created_by`, khoảng thời gian, sắp xếp, phân trang | M | SP A.8 | Lọc đúng; mặc định mới nhất trước |
| FR-EXP-08 | Mỗi experiment lưu `config` (đã làm sạch), thời điểm, thời lượng, người chạy | M | SP A.4.5 | Đủ để tái hiện điều kiện |
| FR-EXP-09 | Nhật ký sự kiện theo experiment (`experiment_events`) không chứa dữ liệu nhạy cảm | M | SP A.11 | Xem 10.3 |

### 4.4. Lab 01 — Deepfake (DF)

| ID | Yêu cầu | Ưu | Nguồn | AC |
|---|---|---|---|---|
| FR-DF-01 | `POST /media-assets` (multipart, `201` + `Location`): nhận video, kiểm tra loại bằng nội dung (magic bytes), dung lượng, thời lượng; lưu tên ngẫu nhiên trong `storage/` | M | SP A.5.1, A.8 | Tệp đổi đuôi, quá lớn, tên chứa `../` đều bị từ chối |
| FR-DF-02 | Mẫu có sẵn nạp từ `datasets/demo/MANIFEST.csv` (nhãn `original`/`synthetic`, nguồn, `consent_ref`, kỹ thuật). Mẫu thiếu `consent_ref` bị từ chối | M | SP A.4.1, A.11 | — |
| FR-DF-03 | `POST /labs/deepfake/experiments` nhận `asset_id`, tạo experiment; job: lấy siêu dữ liệu (thời lượng, codec, fps, độ phân giải), trích tối đa N khung hình, tách âm thanh nếu có | M | SP A.4.1 | Khung hình và `metadata.json` nằm đúng cấu trúc 9.3 |
| FR-DF-04 | Nếu **chưa có detector được phê duyệt**: kết quả ghi rõ `detector: not_integrated`, `verdict` không có, và `limitations` nêu "chưa có phát hiện tự động" | M | SP A.4.1 bước 4, A.7 | Không xuất hiện điểm số hay nhãn "giả/thật" |
| FR-DF-05 | Nếu có detector: trả `suspicion_score` và `verdict ∈ {suspected, not_detected, insufficient_data}`; ngưỡng do TV3 đặt trước và lưu cùng kết quả | S | SP A.9.1 | Không dùng khóa `probability`/`accuracy` |
| FR-DF-06 | Mọi kết quả Deepfake kèm cảnh báo cố định: "không phải kết luận chắc chắn về danh tính/sự thật" | M | SP A.4.1 bước 5 | Có trong `limitations` |
| FR-DF-07 | `PUT /experiments/{id}/verification` lưu lựa chọn xác minh (số liên hệ đã biết, kênh thứ hai, phê duyệt độc lập) để so sánh rủi ro trước/sau | S | SP A.4.1 bước 6 | Lựa chọn hiện trong chi tiết và báo cáo |
| FR-DF-08 | Dung lượng và thời lượng video tối đa cấu hình được (giá trị khởi đầu, cần đo trên máy thật) | M | SP A.8 | — |

### 4.5. Lab 02 — Prompt Injection (PI)

| ID | Yêu cầu | Ưu | Nguồn | AC |
|---|---|---|---|---|
| FR-PI-01 | Kho ca kiểm thử `prompt_cases` ≥ 5 ca, nạp bằng seed | M | SP A.8 | `GET /labs/prompt_injection/cases` trả ≥ 5 ca |
| FR-PI-02 | `POST /labs/prompt_injection/experiments` nhận `case_codes`, `defenses` (mặc định `[false,true]`), `repeat`; một experiment chứa cả hai nhánh | M | D-07 | Cùng ca, cùng mô hình, cùng số lần lặp ở hai nhánh |
| FR-PI-03 | Mỗi lần gọi mô hình lưu một dòng `prompt_runs` (phản hồi, độ trễ, tên mô hình, nhánh) | M | SP A.9.2 | — |
| FR-PI-04 | Gọi Ollama (chạy trên **Colab**, D-18) chỉ tới địa chỉ cấu hình `OLLAMA_URL`: bắt buộc `https`, host phải thuộc `OLLAMA_ALLOWED_HOSTS` (ví dụ hậu tố `trycloudflare.com`/`ngrok-free.app`), kèm `Authorization: Bearer OLLAMA_API_KEY`; không theo chuyển hướng (redirect) sang host khác; không có tham số URL từ client; token không xuất hiện trong log, `/health` hay kết quả | M | SP A.11, D-18 | Body có URL → 422; `OLLAMA_URL` sai host/`http` → từ chối khởi tạo client, `/health` báo `down` |
| FR-PI-05 | `task_violated` mặc định `NULL`; chỉ quy tắc tự động (`judged_by=rule`) hoặc người dùng (`human`) mới đặt giá trị | M | SP A.4.2, D-08 | Không có giá trị mặc định 0 |
| FR-PI-06 | `PATCH /experiments/{id}/prompt-runs/{run_id}` cho phép người chấm/ghi đè `task_violated` | S | UC-08 | Ghi `judged_by=human`, ghi event |
| FR-PI-07 | Tổng hợp ASR chỉ tính khi mọi lần chạy trong nhánh đã được chấm; nếu còn `NULL` hiển thị "chưa đủ dữ liệu" | M | SP A.4.2 | — |
| FR-PI-08 | Kết quả liệt kê **cả ca phòng thủ không hiệu quả** | M | SP A.4.2 | `metrics.failed_cases` không bị lọc bỏ |
| FR-PI-09 | Trần cứng số lần gọi mô hình mỗi experiment (khởi đầu: 60) | M | SP A.11 | Vượt → 422 `VALIDATION_ERROR` |
| FR-PI-10 | Chịu lỗi dịch vụ từ xa: timeout kết nối ngắn (≈5 s) và timeout đọc dài (≈120 s/lần gọi, đo trên Colab thật); không thử lại vô hạn (tối đa 1 lần thử lại cho lỗi mạng); Colab ngắt phiên/đường hầm đổi → experiment `failed` với `error.code=DEP_UNAVAILABLE`, **các `prompt_runs` đã có vẫn được lưu** và không bị tính vào tỉ lệ | M | D-18 | Tắt Colab giữa chừng → `failed`, API vẫn sống, kết quả từng phần không bị gán nhãn thành công |
| FR-PI-11 | Kết quả ghi rõ nguồn chạy: `metrics.model_name`, `metrics.endpoint_kind="colab"`, và `limitations` nêu mô hình/lượng tử hóa chạy trên dịch vụ ngoài nên kết quả có thể khác khi chạy cục bộ; thành phần `ollama_chat` có `kind=real` chỉ khi có phản hồi thật | M | D-18, N5 | Thiếu `model_name` → `result_guard` từ chối |
| FR-PI-12 | Dữ liệu gửi sang Colab chỉ gồm văn bản kịch bản **tổng hợp** trong `prompt_cases` (không dữ liệu cá nhân, không nội dung do người dùng tải lên) | M | D-18 | Không có đường nào đưa trường văn bản tự do của người dùng vào prompt gửi đi |

### 4.6. Lab 03 — API Security (API)

| ID | Yêu cầu | Ưu | Nguồn | AC |
|---|---|---|---|---|
| FR-API-01 | Endpoint đối tượng thử `GET/POST /sandbox/echo` tách biệt API điều khiển, có bộ đếm riêng. Chỉ nhận lời gọi mang token Lab nội bộ (`X-Lab-Token`). Bật/tắt rate limit **theo từng request** bằng header `X-Lab-Rate-Limit: on\|off` (mặc định `on`); bộ đếm tách theo chế độ và theo khóa chạy (`X-Lab-Run`); `window`/`max_requests` mặc định từ `.env` | M | SP A.4.3 | Rate limit sandbox không ảnh hưởng `/api/*`; cùng một chuỗi request cho kết quả khác nhau ở `on` và `off` |
| FR-API-02 | `POST /labs/api_lab/experiments` nhận `scenario_code`, `rate_limit_modes` (mặc định `[false,true]`); gửi **request HTTP thật** tới `/sandbox/echo` | M | SP A.8 | Có mã 200/429 thật và độ trễ đo thật |
| FR-API-03 | Ghi nhận: số gửi, số chấp nhận (200), số bị giới hạn (429), độ trễ p50/p95 | M | SP A.4.3 | — |
| FR-API-04 | Trần cứng số request mỗi lần chạy (khởi đầu: 100) và thời gian tối đa; không nhận URL đích | M | SP A.4.3, A.11 | — |
| FR-API-05 | Mỗi 429 của kịch bản ghi một `experiment_events` (`event_type=rate_limited`) vào experiment đang chạy | M | SP A.8 | — |
| FR-API-06 | `limitations` luôn nêu: Rate Limiting đơn lẻ không chống được mọi dạng DoS/DDoS | M | SP A.9.3 | — |

### 4.7. Lịch sử và báo cáo (RPT)

| ID | Yêu cầu | Ưu | Nguồn | AC |
|---|---|---|---|---|
| FR-RPT-01 | `GET /experiments/{id}` trả cấu hình, trạng thái, kết quả, `limitations`, events | M | SP A.4.5 | — |
| FR-RPT-02 | `GET /experiments/{id}/report` (`Accept: text/markdown`) xuất Markdown: mục đích, điều kiện thử nghiệm, kết quả, giới hạn | M | SP A.4.5 | Có đủ bốn phần |
| FR-RPT-03 | Báo cáo hiển thị `result_kind` và các `components` | M | SP A.0 | — |
| FR-RPT-04 | Xuất PDF | C | — | — |

### 4.8. Quản trị và vận hành (OPS)

| ID | Yêu cầu | Ưu | Nguồn | AC |
|---|---|---|---|---|
| FR-OPS-01 | `GET /api/v1/health` (công khai) báo trạng thái DB, Ollama (từ xa), FFmpeg, worker, phiên bản; kiểm tra Ollama có timeout ngắn (≈2 s, mạng) và nhớ kết quả ≈30 s; chỉ trả trạng thái và mã lý do cố định, không trả địa chỉ/token/đường dẫn/nội dung ngoại lệ. `200` khi DB và worker chạy (kể cả `degraded`); `503` khi DB hoặc worker hỏng | M | D-18 | Colab tắt/đường hầm hỏng → `ollama: down`, `/health` vẫn `200`, trả nhanh, không làm sập API; `OLLAMA_URL` sai host/`http` → `down` (`invalid_url`) và không gọi mạng |
| FR-OPS-02 | Sự kiện bảo mật (`login_failed`, `file_rejected`, `lab_toggled`) ghi ra log ứng dụng, có thời gian, mức, loại; không chứa mật khẩu/token | S | SP A.5, D-09 | Grep log thấy sự kiện, không thấy bí mật |
| FR-OPS-03 | `scripts/start.ps1` kiểm tra điều kiện, tạo venv, chạy migration, seed, khởi động | M | SP A.8 | Máy Windows sạch chạy được bằng một lệnh |
| FR-OPS-04 | Cấu hình qua `.env`; có `.env.example`; không commit bí mật | M | SP A.8 | — |

---

## 5. Kiến trúc

### 5.1. Sơ đồ thành phần

```
┌───────────────────────────── React + Vite (TV5) ─────────────────────────────┐
└───────────────────────────────────────┬──────────────────────────────────────┘
                                        │ HTTP/JSON, Bearer JWT
┌───────────────────────────────────────▼──────────────────────────────────────┐
│ FastAPI (một tiến trình)                                                     │
│                                                                              │
│  [Cổng 1]  deps: auth, require_role │ ratelimit (API điều khiển)             │
│            core/files: kiểm tra tệp, đường dẫn an toàn │ Pydantic schemas    │
│                                                                              │
│  api/ routers ── gọi ──► orchestrator ──► jobs/queue ──► worker (1 thread)   │
│                              │                               │               │
│                              │ ghi DB (SQLAlchemy)           │ gọi            │
│                              ▼                               ▼               │
│                        SQLite (WAL)                    LabService (registry) │
│                                                          ├ deepfake  TV2+TV3 │
│  [Cổng 3]  result_guard ◄── LabResult ◄──────────────────├ prompt    TV4     │
│            (nhãn, limitations, khóa cấm, ceiling)        ├ api_lab   TV4     │
│                                                          └ poisoning (planned)│
│  /sandbox/*  (đối tượng thử, bộ đếm riêng) ◄── HTTP thật ── api_lab          │
└──────────────────────────────────────────────────────────────────────────────┘
        storage/ (uploads, frames, exports)        Ollama @ Colab qua HTTPS (chỉ PI)
```

### 5.2. Máy trạng thái experiment

```
 (yêu cầu chạy hợp lệ)
        │  tạo bản ghi
        ▼
    queued ──worker nhận──► running ──run() OK + result_guard đạt──► done
        │                      │
        │ khởi động lại        ├── ngoại lệ / timeout / result_guard từ chối ──► failed
        │ (xếp hàng lại)       │
        └──────────────────────┘ khởi động lại: running → failed, event "interrupted"
```

Quy tắc: chỉ các chuyển trạng thái trên là hợp lệ; `done` và `failed` là trạng thái cuối. Yêu cầu sai ở Cổng 1 (4xx) **không** tạo experiment.

Mã lỗi của job (`experiments.error_code`, trả ở `error.code`; khác mã lỗi HTTP):

| `error.code` | Khi nào | Event cuối |
|---|---|---|
| `JOB_TIMEOUT` | Quá `JOB_TIMEOUT_<LAB>` (FR-EXP-05), hoặc service báo `LabRunError("JOB_TIMEOUT")` | `timeout` |
| `RESULT_REJECTED` | `result_guard` hoặc kiểm tra `records` từ chối (FR-EXP-06); lý do ở `lab_results.guard_details` | `result_rejected` |
| `INTERRUPTED` | Máy chủ khởi động lại khi job đang chạy (FR-EXP-04) | `interrupted` |
| `DEP_UNAVAILABLE` | Service báo `LabRunError("DEP_UNAVAILABLE")`: phụ thuộc ngoài (Ollama trên Colab) mất kết nối giữa chừng (FR-PI-10); các dòng `prompt_runs` hợp lệ đã có vẫn được lưu | `dep_unavailable` |
| `INTERNAL_ERROR` | Service ném ngoại lệ ngoài dự kiến (hoặc `LabRunError` mã lạ), hoặc lỗi ghi CSDL; chi tiết chỉ ở log, không trả client | `job_failed` |

`LabRunError` (FR-PI-10): service ném `LabRunError(code, message, partial_records)` khi biết chắc phụ thuộc ngoài hỏng. Orchestrator đánh dấu `failed` với `error.code = code` và lưu `partial_records` (cùng giao dịch). Quy tắc: (1) `code` chỉ nhận `DEP_UNAVAILABLE` hoặc `JOB_TIMEOUT`, mã khác thành `INTERNAL_ERROR` (service không tự đặt mã tùy ý, nhất là `RESULT_REJECTED`/`INTERRUPTED`); (2) `message` chỉ ghi log máy chủ, **không** trả client hay ghi vào event (thông điệp lỗi mạng có thể chứa địa chỉ đường hầm Colab); `error.message` là câu cố định theo mã; (3) `partial_records` đi qua cùng kiểm tra `records` của guard (bảng/cột cho phép, kiểu, `task_violated` chỉ kèm `judged_by='rule'`, `case_code` phải có trong `prompt_cases`) và giới hạn `MAX_RESULT_BYTES`; **sai một dòng là bỏ cả nhóm** (không lưu một nửa), event cuối có `data.partial_dropped=true`; (4) không có `lab_results` và không có `result_kind`: kết quả từng phần chỉ là dữ liệu thô, không bao giờ được gán nhãn thành công hay tính vào tỉ lệ (FR-PI-07); (5) event cuối mang `data.partial_runs` là số dòng đã lưu.

Cơ chế timeout: worker chạy `service.run` trong một thread con. Hết giờ, orchestrator gọi `ctx.cancel()` để `ctx.check()` ném `JobCancelled`. Service không gọi `ctx.check()` thì không dừng được (Python không giết được thread): sau thời gian ân hạn (5 s) job vẫn bị đánh dấu `failed` và thread bị bỏ rơi để hàng đợi không kẹt. Vì vậy **service dài phải gọi `ctx.check()` định kỳ**.

### 5.3. Trình tự xử lý một yêu cầu chạy Lab

1. **Cổng 1:** xác thực token → kiểm tra vai trò → rate limit API điều khiển → validate body (Pydantic) → kiểm tra Lab `enabled`.
2. `service.validate(payload, settings)` → trả payload đã chuẩn hóa hoặc ném `LabValidationError` (→ 422, không tạo experiment).
3. Orchestrator tạo experiment `queued` (+ event `queued`), đẩy job, trả `202`.
4. Worker: `running` → gọi `service.run(ctx, payload)` trong timeout.
5. **Cổng 3 (result_guard):** kiểm tra `LabResult` (6.1). Đạt → orchestrator ghi `lab_results` + `records` (đổi `case_code` → `case_id`) + `experiment_events` + chuyển `done` trong **một giao dịch**. Không đạt (kể cả `case_code` không có trong `prompt_cases`) → `failed` + `result_rejected`, **không ghi dòng nghiệp vụ nào**; `lab_results` giữ một dòng `guard_passed=0` (`result_kind=planned` để thỏa ràng buộc cột, không lưu nội dung bị từ chối) kèm `guard_details`.
6. Frontend poll `Location` (`GET /experiments/{id}`, có `ETag`) đến khi `done`/`failed`.

### 5.4. Quy tắc phụ thuộc giữa các lớp

| Quy tắc | Lý do |
|---|---|
| `services/*` **không** import từ `api/`, `models/orm` hay `jobs/` | Kiểm thử service độc lập, không cần DB |
| `services/*` chỉ nhận dữ liệu qua `payload`/`ctx`, trả `LabResult` | Một điểm ghi DB duy nhất (D-12) |
| Chỉ `orchestrator` ghi bảng `experiments`, `experiment_events`, bảng nghiệp vụ | Bảo đảm giao dịch và nhãn trung thực |
| `api/*` không chứa logic nghiệp vụ Lab | Router mỏng; logic nằm ở orchestrator/service |
| Không module nào mở kết nối ra ngoài máy, **trừ** client Ollama tới đích duy nhất trong `.env` (https + danh sách host cho phép + token, D-18); địa chỉ không bao giờ lấy từ client | SP A.11, D-18 |

### 5.5. Lựa chọn công nghệ

| Thành phần | Chọn | Ghi chú |
|---|---|---|
| Web | FastAPI | OpenAPI tự động cho TV5 |
| ORM + migration | SQLAlchemy 2 + Alembic | Migration là sản phẩm bàn giao của TV1 |
| CSDL | SQLite, `WAL`, `busy_timeout`, `foreign_keys=ON` | Một máy, không cài server |
| Xác thực | JWT (HS256) + băm argon2 hoặc bcrypt | Khóa ký từ `.env` |
| Hàng đợi | Queue trong tiến trình + 1 worker thread | D-04 |
| HTTP | `httpx` (gọi sandbox nội bộ; gọi Ollama trên Colab với timeout và `follow_redirects=False`) | `requests` hiện có có thể giữ cho đơn giản; thống nhất một thư viện |
| Kiểm thử | `pytest`, `httpx` TestClient | Test không cần Ollama/GPU |

---

## 6. Ràng buộc nhãn trung thực (lõi của hệ thống)

### 6.1. Điều kiện chấp nhận một `LabResult` (result_guard)

Kết quả chỉ được lưu `done` khi **tất cả** đúng:

| # | Điều kiện | Ví dụ vi phạm |
|---|---|---|
| G1 | `result_kind` ∈ {real, simulated, planned} | `"ok"` |
| G2 | `result_kind` ≤ trần `labs.status` (6.2) | Lab `simulated` trả `real` |
| G3 | `limitations` là danh sách **không rỗng** | thiếu hoặc `[]` |
| G4 | Không chứa khóa `probability`, `accuracy`, `confidence_pct` ở mức kết quả khi chưa có bản ghi hiệu chuẩn | `{"accuracy": 0.97}` tự đặt |
| G5 | Mỗi `components[i].kind` hợp lệ và `result_kind` = mức yếu nhất của các thành phần (6.3) | Không khớp |
| G6 | `artifacts` là đường dẫn tương đối nằm trong `storage/` | `../../etc/x` |
| G7 | Kích thước `result_json` ≤ giới hạn cấu hình (`MAX_RESULT_BYTES`), tuần tự hóa được thành JSON (không NaN) | — |

Cài đặt (`app/services/guard.py`) còn hai kiểm tra bổ sung: `shape` (đúng kiểu `LabResult`) và `records` (chỉ ghi bảng/cột trong `RECORD_COLUMNS`; kiểu từng dòng `prompt_runs`: `case_code` chuỗi, `defense` boolean/0/1 bắt buộc, `attempt` ≥ 1, `latency_ms` ≥ 0; service không được tự đặt `task_violated` trừ khi kèm `judged_by='rule'` — FR-PI-05). Guard không ném ngoại lệ với đầu vào lạ: kết quả sai bị từ chối kèm lý do trong `lab_results.guard_details`.

### 6.2. Trần trạng thái

Thứ tự mạnh → yếu: `real` > `simulated` > `planned`. `result_kind` của experiment **không được mạnh hơn** `labs.status`.

### 6.3. Thành phần và mức "yếu nhất" **[ĐỀ XUẤT, D-10]**

Một kết quả gồm nhiều thành phần, mỗi thành phần có nhãn riêng. Ví dụ Lab Deepfake chưa có detector:

```json
"components": [
  { "name": "preprocess", "kind": "real",    "note": "FFmpeg/OpenCV chạy thật" },
  { "name": "detector",   "kind": "planned", "note": "chưa tích hợp" }
],
"result_kind": "planned"
```

`result_kind` ở mức experiment lấy mức yếu nhất để không gây hiểu rằng "AI đã phát hiện". Giao diện **bắt buộc** hiển thị cả `components` để không che bằng chứng tiền xử lý đã chạy thật. **[MỞ-1]** Nhóm xác nhận cách hiển thị này.

---

## 7. Mô hình dữ liệu MVP

Sơ đồ ER đã chốt: `docs/spec/diagrams/07-er.png` (8 bảng); SQL nằm ở các migration `0001`–`0002` (nguồn chuẩn). Dưới đây là phạm vi **MVP** và các quyết định đã chốt trong SPEC này.

### 7.1. Bảng lõi (tuần 1–2)

| Bảng | Mục đích | Ghi chú MVP |
|---|---|---|
| `users` | Tài khoản | `role` ∈ {admin, user} (CHECK mở rộng sau) |
| `labs` | Danh mục + trần trạng thái | 4 dòng seed |
| `experiments` | Mỗi lần chạy | `id` dạng `exp_` + uuid4 hex; thời gian ISO-8601 UTC |
| `experiment_events` | Nhật ký theo thử nghiệm | Danh mục `event_type` ở mục 10.4 |
| `lab_results` | Kết quả 1–1 với experiment (`result_kind`, `limitations`, `guard_passed`) | CHECK: `guard_passed=1` thì `limitations` không rỗng |

### 7.2. Bảng nghiệp vụ Lab (tuần 3–5, theo tiến độ TV2/TV3/TV4)

| Bảng | Lab | Thêm khi |
|---|---|---|
| `media_assets` | Deepfake | Tuần 4 |
| `prompt_cases`, `prompt_runs` | Prompt Injection | Tuần 3 |

Mỗi bảng thêm bằng migration riêng; không sửa tay tệp `.db`.

### 7.3. Ràng buộc dữ liệu bắt buộc

- `experiments.result_kind`, `labs.status`: CHECK ∈ {real, simulated, planned}.
- `experiments.state`: CHECK ∈ {queued, running, done, failed}.
- `prompt_runs.task_violated`: **nullable**; `judged_by` ∈ {rule, human} hoặc NULL.
- `media_assets.consent_ref`, `source`, `label`: NOT NULL.
- Khóa ngoại bật (`PRAGMA foreign_keys=ON` ở mỗi kết nối).
- Định danh mờ có tiền tố: `usr_`, `lab_`, `exp_`, `res_`, `med_` (`media_assets`), `case_` (`prompt_cases`), `run_` (`prompt_runs`).

---

## 8. Hợp đồng API

Hợp đồng API được tách thành tài liệu riêng **API-01 · Hợp đồng REST API v0.8** và là nguồn chuẩn cho đường dẫn, phương thức, mã trạng thái, dạng lỗi và biểu diễn tài nguyên. Mục này chỉ tóm tắt.

### 8.1. Nguyên tắc

1. API **hướng tài nguyên**: đường dẫn là danh từ số nhiều kebab-case, tiền tố `/api/v1`; hành động nằm ở phương thức HTTP.
2. **Chạy một Lab = tạo tài nguyên `experiment`:** `POST /labs/{lab}/experiments` → `202 Accepted` + `Location`; đọc kết quả qua `GET /experiments/{id}` (có `ETag`).
3. Mọi danh sách trả `{items, page, page_size, total}`.
4. Lỗi theo **RFC 9457** (`application/problem+json`) với trường mở rộng `code`; sai dữ liệu = `422`, JSON hỏng = `400`.
5. Một biểu diễn, nhiều định dạng bằng `Accept` (báo cáo `text/markdown` hoặc `application/json`).
6. `/sandbox/echo` nằm **ngoài** `/api/v1` vì là đối tượng bị thử, không phải tài nguyên nghiệp vụ.

### 8.2. Tài nguyên chính

| Tài nguyên | Phương thức chính |
|---|---|
| `auth/login`, `users/me` | `POST`, `GET` |
| `labs`, `labs/{lab}/scenarios`, `labs/prompt_injection/cases` | `GET` (`PATCH` Lab: admin) |
| `labs/{lab}/experiments` | `POST` (tạo, `202`) |
| `experiments`, `experiments/{id}` | `GET` (+ `DELETE` admin) |
| `experiments/{id}/events`, `/artifacts`, `/report`, `/verification`, `/prompt-runs` | `GET`; `PUT` (verification); `PATCH` (prompt-runs) |
| `media-assets` | `POST` (multipart), `GET`, `DELETE` (admin) |
| `health` | `GET` |

### 8.3. Điều chỉnh đã áp dụng so với SPEC v0.1

- Các endpoint dạng động từ (`/deepfake/analyze`, `/prompt/run`, `/api-lab/run`, `/deepfake/upload`) được thay bằng tạo tài nguyên (`experiments`, `media-assets`).
- `GET /labs` đổi sang `{items,…}`; `status` ∈ `real | simulated | planned`; thêm `code`, `enabled`, `links`; `id` số giữ tạm (deprecated). Tác động tới frontend: API-01 mục 11.2 (**MỞ-2**).
- Khóa `experiment_id` đổi thành `id`; mã lỗi 400 dùng cho sai dữ liệu đổi thành 422.
- Tiền tố `/api/v1` và cách tổ chức tài nguyên là **đề xuất mới** (D-13 đến D-17, API-01 mục 12); SP A.5.3 chỉ nêu "API tối thiểu để phối hợp".

## 9. Hợp đồng giữa các module

### 9.1. `LabService`

Đặt trong `app/services/base.py`. `LabResult` và `Component` là **dataclass** (không phải pydantic) để service sai nhãn vẫn dựng được đối tượng và `result_guard` là nơi duy nhất từ chối, kèm lý do.

```python
ResultKind = Literal["real", "simulated", "planned"]

@dataclass
class Component:
    name: str
    kind: str                              # real | simulated | planned
    note: str | None = None

@dataclass
class LabResult:
    result_kind: str
    summary: str
    components: list[Component]            # bắt buộc, ≥ 1
    limitations: list[str]                 # bắt buộc, không rỗng
    metrics: dict = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)   # đường dẫn tương đối trong storage/
    records: dict[str, list[dict]] = field(default_factory=dict)  # dòng cho bảng nghiệp vụ (D-12)

class RunContext:                          # orchestrator dựng; service chỉ dùng
    settings: Settings
    experiment_id: str
    storage_dir: Path
    def emit(self, level, event_type, message, data=None) -> None: ...  # ghi experiment_events (tối đa MAX_EVENTS_PER_EXPERIMENT)
    def check(self) -> None: ...           # ném JobCancelled khi hết giờ hoặc bị hủy

class LabService(Protocol):
    code: str                              # khớp labs.code và catalog
    def validate(self, payload: dict, settings: Settings) -> dict: ...  # trả payload chuẩn hóa, hoặc ném LabValidationError
    def run(self, ctx: RunContext, payload: dict) -> LabResult: ...     # payload = giá trị validate đã trả
```

`validate` nhận `settings` để đọc các trần (`MAX_PI_CALLS`, `MAX_FRAMES`…); lỗi trả `LabValidationError([{field, message}])`, orchestrator đổi thành 422 và **không** tạo experiment. `base.py` có sẵn `reject_unknown`, `is_code`, `is_int_in`, `require_object` cho việc này.

**Quy ước `records`.** Chỉ bảng và cột trong `RECORD_COLUMNS` (hiện có `prompt_runs`: `case_code`, `attempt`, `defense`, `response`, `latency_ms`, `model_name`, `task_violated`, `judged_by`). Service dùng `case_code`; orchestrator tra `prompt_cases` để đổi sang `case_id` và tự thêm `id`, `experiment_id`, `created_at`. `task_violated` mặc định `None`; chỉ được đặt cùng `judged_by="rule"`. Giá trị `human` chỉ đến từ API chấm tay (FR-PI-06).

**`LabRunError`.** Ném từ `run` khi phụ thuộc ngoài hỏng giữa chừng, thay cho ngoại lệ thường (thường sẽ thành `INTERNAL_ERROR` và mất dữ liệu đã chạy): `raise LabRunError("DEP_UNAVAILABLE", "chi tiết cho log", {"prompt_runs": rows_da_co})`. Chi tiết hành vi ở mục 5.2.

Nghĩa vụ của người cài đặt: (1) không mở kết nối ra ngoài, trừ client Ollama dùng `OLLAMA_URL`/`OLLAMA_API_KEY` đã cấu hình (D-18); (2) luôn điền `limitations`; (3) không đủ điều kiện chạy thật thì trả nhãn thấp hơn, **không bịa số liệu**; `result_kind` bằng mức yếu nhất của `components` (D-10); (4) không ghi DB và không import `app.api`, `app.models`, `app.jobs`, dùng `records`; (5) dùng `ctx.emit` thay vì `print`; (6) gọi `ctx.check()` định kỳ trong vòng lặp dài để dừng sớm khi hết giờ.

### 9.2. TV1 ↔ TV5 (frontend)

- Luồng chung: `POST /labs/{lab}/experiments` → đọc `Location` → poll theo `Retry-After` (1–2 giây) đến `done`/`failed`.
- Bắt buộc hiển thị: nhãn `result_kind`, các `components`, danh sách `limitations`.
- Không giả định có `suspicion_score`; kiểm tra sự tồn tại trước khi hiển thị.
- Xử lý mọi mã lỗi theo `problem+json` (API-01 mục 4.3).

### 9.3. TV2 → TV3 (Deepfake)

```
storage/experiments/<experiment_id>/
├─ metadata.json   {duration_s, fps, width, height, codec, frame_count, has_audio}
├─ frames/000001.jpg …
└─ audio.wav       (nếu có; MVP không phân tích — D-03)
```

TV3 nhận đường dẫn thư mục này và trả `{suspicion_score?, frames_analyzed, quality, threshold}`. Nếu chưa có model đạt: không trả điểm; backend ghi `detector: planned` (FR-DF-04).

### 9.4. TV1 ↔ TV4

| Hạng mục | TV1 | TV4 |
|---|---|---|
| `/sandbox/echo` và bộ đếm | Dựng khung, cấu hình `window`, `max_requests`, bật/tắt | Chọn tham số kịch bản |
| `api_lab` service | Gọi, kiểm tra `LabResult`, trả số liệu qua `LabResult.metrics` (lưu ở `lab_results`) | Viết service: gửi request thật qua `httpx`, đo 200/429, p50/p95 |
| Nhật ký | `experiment_events` qua `ctx.emit()`, log ứng dụng cho sự kiện bảo mật | Gọi `ctx.emit()` cho mốc/cảnh báo, không tự tạo bảng log |
| Ollama trên **Colab** | Biến `OLLAMA_URL`, `OLLAMA_API_KEY`, `OLLAMA_ALLOWED_HOSTS`, timeout; kiểm tra địa chỉ khi khởi động; `GET /health` báo `ollama` (có bộ nhớ đệm ngắn, không lộ địa chỉ/token); `503 DEP_UNAVAILABLE` khi tạo experiment lúc Ollama down | Notebook Colab (cài Ollama, kéo mô hình, mở đường hầm, bảo vệ bằng token), **runbook** khởi động/tắt, chốt mô hình + lượng tử hóa, đo độ trễ thật để TV1 đặt timeout |
| Client + chatbot | Hợp đồng `LabService`, ghi `prompt_runs` qua `records`, `result_guard` | Client Ollama (httpx, timeout, xử lý lỗi mạng), prompt hệ thống, lớp phòng thủ |
| `prompt_cases` | Bảng, seed, API | Nội dung ≥ 5 ca, tiêu chí chấm tự động |

Lab API gọi sandbox bằng **HTTP thật** tới `127.0.0.1` từ worker thread; endpoint sandbox viết đồng bộ để worker không chặn vòng lặp sự kiện của server.

**Quy ước vận hành Colab (D-18).** (1) Địa chỉ đường hầm thường đổi mỗi phiên Colab → sau khi bật Colab, TV4 gửi địa chỉ mới cho người chạy backend, người đó sửa `.env` rồi khởi động lại backend (MVP không có API đổi địa chỉ lúc chạy, để không mở thêm bề mặt tấn công). (2) Colab miễn phí có thể ngắt khi nhàn rỗi hoặc hết hạn mức GPU → luôn kiểm tra `GET /health` trước buổi demo; kết quả đã chạy thật được lưu trong CSDL nên có thể mở lại lịch sử khi Colab tắt (không chạy lại bằng số bịa). (3) Phản hồi từ Colab là dữ liệu không tin cậy: không thực thi, không dùng làm URL/đường dẫn, chỉ hiển thị dạng văn bản đã thoát (escape).

---

## 10. Bảo mật, nhật ký, cấu hình

### 10.1. Ba cổng

| Cổng | Biện pháp MVP | Yêu cầu |
|---|---|---|
| **1. Đầu vào** | JWT, vai trò, rate limit API điều khiển; kiểm tra tệp theo nội dung, dung lượng, thời lượng; tên tệp ngẫu nhiên; `Path.resolve()` + kiểm tra nằm trong `storage/`; Pydantic với khoảng giá trị | FR-AUTH-*, FR-DF-01 |
| **2. Xử lý** | Lab chạy trong worker; FFmpeg gọi bằng `subprocess.run([...], timeout=…)`, **không** `shell=True`; Ollama chỉ tới đích cấu hình (https, danh sách host cho phép, token, không theo redirect — D-18); LLM không được cấp công cụ; (S) kiểm tra hash model khi nạp | FR-PI-04, FR-EXP-05 |
| **3. Đầu ra** | `result_guard` (6.1); nhãn trung thực; cảnh báo cố định; lọc log | FR-EXP-06, FR-DF-06 |

### 10.2. Cấu hình cần siết lại so với repo hiện tại

| Hiện tại | Yêu cầu |
|---|---|
| `allow_origins=["*"]` + `allow_credentials=True` | Chỉ origin của frontend (`http://localhost:5173`, `http://127.0.0.1:5173`), cấu hình qua `.env` |
| Không có bí mật | `JWT_SECRET` bắt buộc từ `.env`; thiếu → không khởi động |
| `status:"active"` cố định | Lấy từ bảng `labs` |

### 10.3. Dữ liệu trong log

Không ghi: mật khẩu, token, nội dung tệp, toàn bộ nội dung prompt/phản hồi ở mức `experiment_events` (nội dung đầy đủ chỉ nằm trong `prompt_runs`, dùng dữ liệu giả của nhóm). IP chỉ lưu dạng che một phần. Log ứng dụng chỉ quản trị viên xem được.

### 10.4. Danh mục `event_type` khởi đầu

`queued`, `job_started`, `job_finished`, `job_failed`, `dep_unavailable`, `result_rejected`, `timeout`, `interrupted`, `frame_extracted`, `llm_call`, `request_sent`, `rate_limited`, `verification_saved`, `judged`.
Loại bảo mật: `login_failed`, `file_rejected`, `rate_limited`, `lab_toggled`.

### 10.5. Biến cấu hình (`.env`)

| Biến | Mặc định / ghi chú |
|---|---|
| `JWT_SECRET` | **bắt buộc** |
| `JWT_EXPIRE_MINUTES` | 60 |
| `DB_PATH` | `./storage/lab.db` |
| `STORAGE_DIR` | `./storage` |
| `CORS_ORIGINS` | danh sách origin frontend |
| `OLLAMA_URL` | địa chỉ đường hầm HTTPS tới Ollama trên Colab (đổi mỗi phiên, **không commit**); để trống = Lab Prompt Injection báo `DEP_UNAVAILABLE` |
| `OLLAMA_API_KEY` | **bí mật**, token gửi kèm mọi lời gọi; không có giá trị mặc định |
| `OLLAMA_ALLOWED_HOSTS` | danh sách hậu tố host cho phép, ví dụ `trycloudflare.com,ngrok-free.app`; host ngoài danh sách bị từ chối |
| `OLLAMA_MODEL` | tên mô hình đã kéo trên Colab |
| `OLLAMA_CONNECT_TIMEOUT`, `OLLAMA_READ_TIMEOUT` | 5 s / 120 s (khởi đầu, đo trên Colab thật) |
| `MAX_UPLOAD_MB`, `MAX_VIDEO_SECONDS`, `MAX_FRAMES` | giá trị khởi đầu, **cần đo trên máy thật** |
| `MAX_API_LAB_REQUESTS` | 100 (khởi đầu); `LabService.validate` của Lab API phải từ chối (422) kịch bản vượt trần |
| `SANDBOX_TOKEN` | **bí mật**, token Lab nội bộ cho `/sandbox/echo`; để trống = tự sinh ngẫu nhiên mỗi lần khởi động (Lab đọc từ `ctx.settings.sandbox_token`) |
| `SANDBOX_BASE_URL` | đích cố định mà Lab API gọi (`ctx.settings.sandbox_base_url`); chỉ loopback, mặc định `http://127.0.0.1:8000` |
| `SANDBOX_WINDOW_SECONDS`, `SANDBOX_MAX_REQUESTS` | cửa sổ trượt (10 s) và số request/cửa sổ (20) khi bật rate limit; TV4 chỉnh theo kịch bản |
| `MAX_PI_CALLS` | 60 (khởi đầu) |
| `MAX_EVENTS_PER_EXPERIMENT` | 200 (khởi đầu); chặn một Lab lỗi vòng lặp làm phình CSDL |
| `MAX_RESULT_BYTES` | 262144 (256 KiB); giới hạn G7 |
| `JOB_TIMEOUT_DEFAULT` | 120 giây (khởi đầu); dùng cho Lab chưa đặt biến riêng |
| `JOB_TIMEOUT_DEEPFAKE`, `JOB_TIMEOUT_PROMPT_INJECTION`, `JOB_TIMEOUT_API_LAB`, `JOB_TIMEOUT_POISONING` | giây, tùy chọn, cần đo; Lab mới thêm một biến `job_timeout_<code>` trong `app/config.py` |

---

## 11. Truy vết nghiệm thu (SP A.8 → yêu cầu → kiểm thử)

| Hạng mục nghiệm thu SP | Yêu cầu | Kiểm thử đề xuất |
|---|---|---|
| Chạy một máy Windows, có lệnh khởi động, README, không cần dịch vụ trả phí | FR-OPS-03, FR-OPS-04 | Chạy `start.ps1` trên máy sạch |
| Prompt Injection: Ollama thật, ≥ 5 ca có trước/sau, không tự gán thành công | FR-PI-01…09 | Chạy bộ 5 ca; kiểm tra `task_violated` mặc định `NULL`; kiểm tra ASR chỉ hiện khi đã chấm đủ |
| API Security: request thật, mã trạng thái, log; Rate Limiting chặn đúng mức | FR-API-01…06 | Chạy hai nhánh; đối chiếu số 200/429 với `metrics` và `experiment_events` |
| Deepfake cơ bản: ≥ 1 cặp video, nhãn, upload, tiền xử lý, kịch bản xác minh | FR-DF-01…04, 06, 07, 08 | Upload cặp mẫu; kiểm tra `metadata.json`, khung hình, cảnh báo |
| Deepfake AI (nếu tích hợp): chỉ số, ngưỡng, tập test, hạn chế | FR-DF-05 | TV3 cung cấp bảng đo thật |
| Dashboard: lịch sử, lọc theo Lab, chi tiết, rõ loại kết quả | FR-EXP-07, FR-RPT-01…03, FR-LAB-01 | Lọc `lab`/`kind`; so khớp nhãn hiển thị |
| An toàn: không khóa trong mã, không nhắm ngoài sandbox, tệp upload có giới hạn | FR-OPS-04, FR-PI-04, FR-API-04, FR-DF-01 | Quét repo tìm bí mật; thử body có URL; thử tệp độc |

### 11.1. Kiểm thử đơn vị/tích hợp tối thiểu (không cần Ollama/GPU)

1. Đăng nhập sai/đúng; endpoint không token → 401; `user` gọi endpoint admin → 403.
2. Tệp đổi đuôi, quá lớn, tên `../` → bị từ chối, không ghi ra ngoài `storage/`.
3. Vòng đời: `queued → running → done`; service ném lỗi → `failed`; khởi động lại → job treo thành `failed`.
4. `result_guard`: service `simulated` trả `real` → `failed`; thiếu `limitations` → `failed`; có `accuracy` tự đặt → `failed`.
5. Sandbox: bật/tắt rate limit cho kết quả khác nhau; vượt trần `sent` → 422; body có URL không ảnh hưởng đích.
6. Hai yêu cầu đồng thời → cả hai kết thúc, không `database is locked`.
7. Client Ollama (dùng `httpx.MockTransport`, **không gọi Colab thật**): `OLLAMA_URL` dùng `http`/host ngoài danh sách → bị từ chối; token có trong header nhưng không có trong log/kết quả; phản hồi redirect không được theo; lỗi mạng giữa chừng → `failed`/`DEP_UNAVAILABLE`, các lượt đã xong vẫn lưu.

---

## 12. Việc cần làm trong repo (backlog TV1, theo thứ tự)

Dựa trên khoảng cách ở 1.1. **Giai đoạn 0** không phụ thuộc quyết định mở và có thể bắt đầu ngay.

| ID | Việc | Phụ thuộc | Tuần |
|---|---|---|---|
| T-01 | Chuyển `main.py` vào `backend/app/`, tách `config.py`, `deps.py`; thêm `core/`, `jobs/`, `migrations/`, `tests/`, `scripts/`, `datasets/demo/`, `storage/` (gitignore) | — | 1 |
| T-02 | Cập nhật `requirements.txt`: Alembic, thư viện JWT, băm mật khẩu, `httpx`, `pytest`; chốt phiên bản | — | 1 |
| T-03 | `.env.example`; siết CORS; bắt buộc `JWT_SECRET` | — | 1 |
| T-04 | Migration `0001` (5 bảng lõi) + seed Lab/tài khoản | D-05, D-06 | 1 |
| T-05 | Auth + `require_role`; `POST /auth/login`, `GET /users/me` | T-04 | 1 |
| T-06 | `GET /labs`, `GET /labs/{lab}` đọc từ DB, đổi `status`; **thống nhất với TV5** (API-01 mục 11.2) | MỞ-2 | 1 |
| T-07 | `LabService`, `LabResult`, `result_guard`, `registry`; 4 stub trả `simulated` | D-10 | 1 |
| T-08 | Orchestrator, queue, worker, vòng đời, `experiment_events`, xử lý khởi động lại | T-07 | 2 |
| T-09 | `GET /experiments`, `/{id}`, `/events`, `/report` (theo API-01) | T-08 | 2 |
| T-10 | `/health`, `start.ps1` | T-01 | 2 |
| T-11 | `docs/api-contract.md` (xuất từ API-01) + JSON ví dụ; `docs/integration.md` (từ mục 9) | T-07 | **1 (khóa hợp đồng)** |
| T-12 | Khung `/sandbox/echo` + bộ đếm | D-09 | 2 |
| T-13 | Upload an toàn (`core/files.py`) + `media_assets` | T-04 | 2–4 |
| T-14 | Bộ test 11.1 | T-08 | 2–3 |
| T-15 | Sơ đồ kiến trúc + ER trong `docs/` | — | 1 |

Bàn giao cuối tuần 1: backend khởi động được, đăng nhập được, `/labs` từ DB, stub 4 Lab chạy qua vòng đời, **hợp đồng API và `LabService` được công bố**.

---

## 13. Rủi ro và điểm mở

### 13.1. Điểm mở cần nhóm quyết

| ID | Câu hỏi | Mặc định của TV1 |
|---|---|---|
| MỞ-1 | Hiển thị `result_kind` = mức yếu nhất **kèm** `components` | Có (D-10) |
| MỞ-2 | Đổi hợp đồng `GET /labs` (và tiền tố `/api/v1`) và ý nghĩa thẻ "active" ở Dashboard | Đổi, TV5 cập nhật |
| MỞ-3 | Ai giữ Lab 04 nếu muốn đưa vào MVP | Không đưa vào (D-02) |
| MỞ-4 | Danh sách ca Prompt Injection và tiêu chí chấm tự động | TV4 đề xuất ≥ 5 ca |
| MỞ-5 | Mô hình Ollama dùng trên Colab (tên + lượng tử hóa), loại đường hầm (cloudflared/ngrok) và cách bảo vệ token | TV4 chốt, TV1 đưa vào `.env`/`.env.example` |
| MỞ-8 | Phương án khi Colab không dùng được trong buổi demo (chạy lại từ lịch sử đã lưu / máy có GPU của một thành viên) | Nhóm quyết trước tuần 5 |
| MỞ-6 | Có detector Deepfake hay không (quyết định ở cuối tuần 4) | Mặc định chưa có |
| MỞ-7 | Nhân sự 5 trong 6 người, tên điền vào phân công | Nhóm quyết |

### 13.2. Rủi ro

| Rủi ro | Mức | Biện pháp |
|---|---|---|
| Hợp đồng đổi sau khi các TV đã viết | Cao | Khóa cuối tuần 1; đổi qua PR + thông báo; `docs/api-contract.md` là nguồn chuẩn |
| Một người backend quá tải | Cao | Hướng 2, hoãn RBAC/hash-model; stub từ tuần 1 |
| Ollama (Colab) chậm, bị ngắt phiên hoặc hết hạn mức GPU | Cao | Trần số lần gọi, timeout kết nối/đọc riêng, `DEP_UNAVAILABLE`, lưu kết quả từng phần, kiểm tra `/health` trước demo, chuẩn bị lịch sử chạy thật (MỞ-8) |
| Đường hầm Colab lộ ra Internet (ai cũng gọi được mô hình) | Trung bình | Token bắt buộc, không commit địa chỉ/token, tắt Colab sau buổi chạy, danh sách host cho phép phía backend |
| Dữ liệu rời khỏi máy sang dịch vụ ngoài | Thấp | Chỉ gửi ca kiểm thử tổng hợp (FR-PI-12); nêu trong `limitations` và báo cáo |
| Worker đơn làm nghẽn khi video dài | Trung bình | Giới hạn thời lượng/khung hình; hàng đợi hiển thị `queued` |
| Service của người khác vi phạm nhãn | Trung bình | `result_guard` tự từ chối; test ở T-14 |
| Số liệu ngưỡng khởi đầu không phù hợp máy thật | Thấp | Đo ở tuần 2–3, cập nhật `.env.example` |

### 13.3. Những điều hệ thống không cam kết

Phát hiện chính xác mọi Deepfake mới; xác minh danh tính người trong video; phòng thủ tuyệt đối mọi tấn công; Rate Limiting đơn lẻ chống DDoS; phát hiện hết dữ liệu đầu độc.

---

## Phụ lục A. Thuật ngữ

| Thuật ngữ | Nghĩa trong tài liệu |
|---|---|
| Experiment | Một lần chạy Lab, có trạng thái, cấu hình, kết quả, nhật ký |
| `result_kind` | Nhãn trung thực của kết quả: `real` / `simulated` / `planned` |
| Trần (ceiling) | `labs.status` giới hạn `result_kind` mạnh nhất mà Lab được phép trả |
| Component | Một thành phần của kết quả với nhãn riêng (vd `preprocess`, `detector`) |
| `result_guard` | Bộ kiểm tra ở Cổng 3 trước khi lưu kết quả |
| Sandbox | Endpoint nội bộ `/sandbox/*` do nhóm kiểm soát, đối tượng bị thử ở Lab 03 |
| ASR | Tỷ lệ ca làm sai nhiệm vụ = số lần sai nhiệm vụ / số lần chạy đã chấm, tính riêng từng nhánh |

## Phụ lục B. Lịch sử phiên bản

| Phiên bản | Ngày | Nội dung |
|---|---|---|
| 0.1 | 15/11/2026 | Bản nháp đầu: phân tích hiện trạng, quyết định mặc định, yêu cầu, kiến trúc, hợp đồng, backlog |
| 0.2 | 15/11/2026 | Chuyển API sang REST theo tài nguyên; tách hợp đồng API thành API-01; thêm D-13 đến D-17; mục 8 thành tóm tắt |
| 0.3 | 16/11/2026 | Đăng nhập bằng email; bỏ bảng security_events, api_lab_runs, model_registry khỏi MVP (khớp ER chốt, D-09); Ollama của TV4 chạy trên Google Colab: thêm D-18; sửa 3.1, 4.5 (FR-PI-04, thêm FR-PI-10..12), 5.1, 5.4, 5.5, 9.1, 9.4, 10.1, 10.5, FR-OPS-01, 11.1, MỞ-5/MỞ-8, bảng rủi ro |
| 0.4 | 18/11/2026 | Chốt chữ ký `LabService` theo bản cài đặt T-07: `validate(payload, settings)`, `run(ctx, payload)`; `LabResult`/`Component` là dataclass; thêm quy ước `records` (`case_code`, `judged_by='rule'`); guard có thêm `shape` và `records`; định danh có tiền tố (`med_`…); thêm `MAX_EVENTS_PER_EXPERIMENT`, `MAX_RESULT_BYTES`; sửa 5.3, 6.1, 7.3, 9.1, 10.5; API-01 lên v0.3 |
| 0.5 | 19/11/2026 | Chốt hành vi orchestrator theo T-08: mã lỗi job (`JOB_TIMEOUT`, `RESULT_REJECTED`, `INTERRUPTED`, `INTERNAL_ERROR`) và cơ chế timeout/hủy (5.2); khởi động lại xếp lại job `queued` (FR-EXP-04); kết quả bị từ chối vẫn lưu `lab_results` với `guard_passed=0` và không ghi dòng nghiệp vụ (5.3); guard kiểm kiểu `prompt_runs` (6.1); thêm event `job_failed` (10.4); biến `JOB_TIMEOUT_*` (10.5) |
| 0.6 | 20/11/2026 | Thêm `LabRunError` (FR-PI-10): service báo lỗi có mã (`DEP_UNAVAILABLE`, `JOB_TIMEOUT`) và lưu `partial_records` hợp lệ; mã lạ → `INTERNAL_ERROR`; `message` không trả client; không có `lab_results` cho job lỗi này; thêm event `dep_unavailable` (5.2, 9.1, 10.4); API-01 lên v0.6 |
| 0.7 | 20/11/2026 | T-10: chốt `GET /api/v1/health` (FR-OPS-01): thành phần `database`, `worker`, `ffmpeg`, `ollama`; trạng thái tổng `ok`/`degraded`/`down`; `503` chỉ khi DB hoặc worker hỏng; Ollama cache 30 s, timeout 2 s, mã lý do cố định; API-01 lên v0.7 |
| 0.8 | 21/11/2026 | T-12: chốt `/sandbox/echo` (FR-API-01): token Lab nội bộ, bật/tắt rate limit theo từng request (`X-Lab-Rate-Limit`), bộ đếm tách theo chế độ và khóa chạy, 429 + `Retry-After`; thêm `SANDBOX_*` (10.5); API-01 lên v0.8 |
