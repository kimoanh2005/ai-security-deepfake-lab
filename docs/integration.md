# HƯỚNG DẪN TÍCH HỢP LAB (`LabService`) — dành cho TV2, TV3, TV4

| | |
|---|---|
| **Tài liệu** | INT-01 · Hướng dẫn viết service cho một Lab |
| **Phiên bản** | 0.2 (20/11/2026) — theo SPEC-01 v0.6 mục 5, 6, 9 và mã `backend/app/services/`, `backend/app/jobs/orchestrator.py` |
| **Người phụ trách** | TV1 |
| **Đọc kèm** | `docs/api-contract.md` (REST mà frontend gọi), `docs/spec/spec.md` (đặc tả) |

## 1. Bạn cần làm gì (và không cần làm gì)

Mỗi Lab là **một file** trong `backend/app/services/labs/` chứa một lớp có hai hàm:

| Hàm | Làm gì |
|---|---|
| `validate(payload, settings)` | Kiểm tra thân yêu cầu, trả payload **đã chuẩn hóa** hoặc ném `LabValidationError` |
| `run(ctx, payload)` | Chạy Lab, trả một `LabResult` |

Bạn **không** viết endpoint, không đụng cơ sở dữ liệu, không quản lý hàng đợi, không lưu kết quả. TV1 lo: xác thực, REST, hàng đợi một worker, timeout, chuyển trạng thái `queued → running → done/failed`, **kiểm tra nhãn trung thực** (`result_guard`) và ghi CSDL. Hiện cả bốn Lab đã có **bản khung (stub)** chạy được từ đầu đến cuối; việc của bạn là thay thân `run` (và nới `validate` nếu cần) bằng bản thật.

| Lab | File stub | Người làm | Đầu vào hiện tại |
|---|---|---|---|
| Deepfake | `labs/deepfake.py` | TV2 (tiền xử lý), TV3 (detector) | `asset_id` (`med_…`), `frame_limit` |
| Prompt Injection | `labs/prompt_injection.py` | TV4 | `case_codes`, `defenses`, `repeat` |
| API Security | `labs/api_lab.py` | TV4 | `scenario_code`, `rate_limit_modes` |
| Data Poisoning | `labs/poisoning.py` | — (ngoài MVP, D-02) | không chạy được |

## 2. Bắt đầu trong 5 phút

```powershell
cd backend
python -m venv .venv ; .venv\Scripts\activate          # Python 3.10–3.12
pip install -r requirements.txt
copy .env.example .env                                   # điền JWT_SECRET, xem .env.example
python -m pytest -q                                      # phải xanh trước khi bạn sửa gì
```

Đọc theo thứ tự: `app/services/base.py` (hợp đồng, ngắn), file stub của Lab bạn, `app/services/guard.py` (luật nhãn), `tests/test_lab_services.py` (mẫu test).

**Quy tắc phụ thuộc (SPEC 5.4):** file trong `services/` **không** import `app.api`, `app.models`, `app.jobs`. Chỉ import `app.config` và `app.services.*`. Có thể thêm module trợ giúp riêng trong `services/` (ví dụ `services/deepfake_pipeline.py`).

## 3. Hợp đồng

Nguồn chuẩn là `backend/app/services/base.py`; dưới đây là bản đọc nhanh.

```python
@dataclass
class Component:                 # một thành phần của kết quả, có nhãn riêng
    name: str
    kind: str                    # "real" | "simulated" | "planned"
    note: str | None = None

@dataclass
class LabResult:
    result_kind: str             # = nhãn YẾU NHẤT trong components (D-10)
    summary: str
    components: list[Component]  # ≥ 1
    limitations: list[str]       # bắt buộc, không rỗng
    metrics: dict = {}           # số liệu; xem luật G4 bên dưới
    artifacts: list[str] = []    # đường dẫn TƯƠNG ĐỐI trong storage/
    records: dict[str, list[dict]] = {}   # dòng cho bảng nghiệp vụ (mục 6)

class RunContext:                # orchestrator dựng, bạn chỉ dùng
    settings: Settings           # các trần: max_frames, max_pi_calls, ollama_*, …
    experiment_id: str
    storage_dir: Path            # gốc storage/ (ví dụ lưu vào storage_dir / "experiments" / experiment_id)
    def emit(level, event_type, message, data=None): ...   # ghi nhật ký
    def check(): ...             # ném JobCancelled khi hết giờ / bị hủy
```

`validate`:

- Dùng tiện ích có sẵn trong `base.py`: `require_object`, `reject_unknown` (**từ chối khóa lạ**), `is_code`, `is_int_in`. Gom **mọi** lỗi rồi ném một lần `LabValidationError([{"field": "...", "message": "..."}])`; frontend nhận `422` với `errors[]` đúng như bạn trả, và **không** có experiment nào được tạo.
- **Không nhận URL, host, đường dẫn tự do** từ client (chống SSRF/path traversal). Chỉ nhận khóa đã biết: mã kịch bản, mã ca, `med_…`. Thấy `http://`, `/`, `..` ở đâu thì từ chối.
- Áp các trần từ `settings` (ví dụ `max_frames`, `max_pi_calls`) và trả **giá trị mặc định đã điền** (`config` của experiment lưu chính giá trị này, API-01 mục 5.5).
- Hàm phải thuần (không I/O chậm, không gọi mạng): chạy trong request HTTP.

`run`:

- Nhận đúng giá trị `validate` đã trả. Chạy trong **worker thread** (một job tại một thời điểm); được phép chặn, nhưng phải gọi `ctx.check()` định kỳ (mục 7).
- Trả `LabResult`. Ném ngoại lệ thường thì experiment thành `failed` (`INTERNAL_ERROR`) và **mất** dữ liệu đã chạy; thông điệp ngoại lệ **không** được trả cho client, chỉ ghi log máy chủ. Khi phụ thuộc ngoài hỏng giữa chừng (Ollama/Colab), ném `LabRunError` (mục 6b) để báo mã lỗi đúng và giữ lại các lượt đã chạy.

## 4. Luật nhãn trung thực (điều TV1 kiểm bằng máy)

Mọi `LabResult` qua `result_guard` trước khi lưu. Vi phạm → experiment `failed`, `error.code = RESULT_REJECTED`, và lý do nằm ở event `result_rejected` / `lab_results.guard_details`. Bạn không thể "lỡ tay" lưu một kết quả sai nhãn, nhưng cũng phải tự trả đúng.

| Mã | Luật | Lỗi hay gặp → cách sửa |
|---|---|---|
| G1 | `result_kind` ∈ `real`/`simulated`/`planned` | gõ `"ok"`, `"REAL"` → dùng đúng ba giá trị, chữ thường |
| G2 | không mạnh hơn trần `labs.status` của Lab | Lab đang `simulated` mà trả `real` → **hạ nhãn**, đừng nâng `labs.status` để lách (chỉ admin đổi, kèm bằng chứng) |
| G3 | `limitations` là danh sách chuỗi không rỗng | quên trả → luôn nêu giới hạn thật (cỡ mẫu, môi trường, thứ chưa làm) |
| G4 | cấm khóa `probability`, `accuracy`, `confidence_pct` **ở cấp trên cùng** của `metrics`, trừ khi `metrics["calibrated"] is True` | đặt `"accuracy": 0.97` tự do → dùng tên khác mô tả đúng (`correct_of_judged`), hoặc chỉ khi đã hiệu chuẩn có bằng chứng thì mới `calibrated: True` |
| G5 | mỗi `components[i].kind` hợp lệ và `result_kind` = **yếu nhất** của chúng (`real` > `simulated` > `planned`) | tiền xử lý `real` + detector `planned` mà khai `result_kind="real"` → phải là `planned`; **giữ cả hai thành phần** để UI vẫn thấy phần chạy thật |
| G6 | `artifacts` là đường dẫn tương đối, không `..`, không `:`/`\`, không tuyệt đối | trả đường dẫn Windows tuyệt đối → trả `experiments/<id>/frames/000001.jpg` |
| G7 | tuần tự hóa được thành JSON (không `NaN`/`Infinity`) và ≤ `MAX_RESULT_BYTES` (256 KiB) | numpy/OpenCV trả `np.float32`, `NaN` → đổi sang `float`, loại `NaN`; không nhét ảnh/văn bản dài vào `metrics` (dùng `artifacts`) |
| shape | đúng kiểu `LabResult`/`Component` | quên `return` → kết quả `None` bị từ chối |
| records | chỉ bảng/cột cho phép, đúng kiểu (mục 6) | thêm cột lạ → xem `RECORD_COLUMNS` |

Nguyên tắc nền (SPEC 6): **không đủ điều kiện chạy thật thì trả nhãn thấp hơn, không bịa số liệu.** Ví dụ Deepfake khi chưa có detector: không có `suspicion_score`, không có nhãn giả/thật, `metrics.detector = "not_integrated"`, và `limitations` có câu "không phải kết luận chắc chắn" (FR-DF-04, FR-DF-06). Stub trong repo là mẫu đúng.

## 5. Nhật ký: `ctx.emit`

```python
ctx.emit("info", "frame_extracted", "Đã trích 24 khung hình", {"count": 24})
ctx.emit("warn", "rate_limited", "429 ở request 17")
```

- `level` ∈ `info`/`warn`/`error` (giá trị khác thành `info`). `event_type` ≤ 64 ký tự, `message` ≤ 500 ký tự (bị cắt).
- Dùng loại có sẵn khi phù hợp: `frame_extracted`, `llm_call`, `request_sent`, `rate_limited` (danh mục ở SPEC 10.4). Loại mới được phép, cứ đặt tên ngắn kiểu `snake_case`.
- **Không đưa vào event:** mật khẩu, token, `OLLAMA_API_KEY`, nội dung tệp, toàn bộ prompt/phản hồi (nội dung đầy đủ chỉ lưu qua `records` → `prompt_runs`). `data` là tùy chọn, phải là JSON thuần và ≤ 4096 byte, nếu không bị thay bằng ghi chú.
- Tối đa `MAX_EVENTS_PER_EXPERIMENT` (200) event/experiment; vượt thì bỏ bớt. Đừng `emit` trong vòng lặp từng khung hình/từng request; chỉ ghi mốc và cảnh báo (riêng mỗi lần bị `429` ở Lab API được phép, FR-API-05).
- Không dùng `print`/`logging` để báo tiến trình người dùng cần thấy; dùng `emit`.

Event do orchestrator tự ghi: `queued`, `job_started`, `job_finished` (hoặc `result_rejected`, `timeout`, `job_failed`, `interrupted`). Bạn không cần ghi các loại này.

## 6. Ghi dữ liệu nghiệp vụ: `records` (chủ yếu TV4, Prompt Injection)

Service không ghi DB. Dòng cần lưu vào bảng nghiệp vụ trả trong `LabResult.records`; orchestrator ghi cùng một giao dịch với kết quả. Hiện chỉ có bảng `prompt_runs`:

```python
records={"prompt_runs": [
    {"case_code": "PI-01", "attempt": 1, "defense": False,
     "response": "…", "latency_ms": 2140, "model_name": "llama3.1:8b-q4"},
    {"case_code": "PI-01", "attempt": 1, "defense": True,
     "response": "…", "latency_ms": 1980, "model_name": "llama3.1:8b-q4",
     "task_violated": False, "judged_by": "rule"},
]}
```

| Quy ước | Chi tiết |
|---|---|
| Cột được phép | `case_code`, `attempt`, `defense`, `response`, `latency_ms`, `model_name`, `task_violated`, `judged_by` (nguồn: `RECORD_COLUMNS` trong `base.py`). **Không** đặt `id`, `experiment_id`, `case_id`, `created_at` |
| `case_code` | mã ca như `PI-01`; orchestrator tra bảng `prompt_cases` để đổi sang `case_id`. Mã không có trong bảng → **cả experiment bị từ chối** và không dòng nào được ghi (hãy `validate` mã ca thật; nội dung ca do TV4 cung cấp qua seed) |
| Kiểu | `defense` boolean (hoặc 0/1) **bắt buộc**; `attempt` ≥ 1 (mặc định 1); `latency_ms` ≥ 0 hoặc `None`; `response`, `model_name` là chuỗi hoặc `None` |
| `task_violated` | mặc định **`None` = chưa chấm**. Chỉ được đặt `True`/`False` khi **kèm `judged_by="rule"`** (chấm bằng quy tắc tự động có tiêu chí rõ, D-08). `judged_by="human"` chỉ đến từ API chấm tay của người dùng. Vi phạm → bị từ chối (FR-PI-05: không tự gán thành công) |
| Trần | tổng lượt gọi ≤ `MAX_PI_CALLS`: kiểm ở `validate`, vượt thì `LabValidationError` (API trả `422`) |

Bảng nghiệp vụ mới (ví dụ cho Deepfake) = thêm cột vào `RECORD_COLUMNS` + migration + nhánh ghi trong orchestrator: **hỏi TV1**, đừng tự thêm.

## 6b. Lỗi có mã và lưu kết quả một phần: `LabRunError` (TV4, Ollama trên Colab)

Colab hay ngắt phiên hoặc đổi địa chỉ đường hầm giữa lúc đang chạy. Đừng để lỗi mạng bay ra thành ngoại lệ thường: ném `LabRunError` kèm các dòng đã chạy được.

```python
from app.services.base import LabRunError

rows = []                                   # các dòng prompt_runs đã hoàn thành
for case, defense, attempt in plan:
    ctx.check()
    try:
        rows.append(call_model(case, defense, attempt))      # trả dòng theo mục 6
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
        ctx.emit("error", "llm_call", "Mất kết nối tới mô hình giữa chừng")
        raise LabRunError("DEP_UNAVAILABLE", f"{type(exc).__name__}", {"prompt_runs": rows}) from exc
```

| Quy tắc | Chi tiết |
|---|---|
| `code` | Chỉ `DEP_UNAVAILABLE` hoặc `JOB_TIMEOUT`. Mã khác (kể cả `RESULT_REJECTED`, `INTERRUPTED`) bị đổi thành `INTERNAL_ERROR`; dữ liệu vẫn giữ |
| `message` | Chỉ ghi **log máy chủ**; không vào event, không trả client. Vẫn **đừng** đưa địa chỉ đường hầm hay token vào đó, và nên chỉ ghi tên loại lỗi như ví dụ. `error.message` mà client thấy là câu cố định theo mã |
| `partial_records` | Cùng dạng và cùng kiểm tra như `records` (mục 6): chỉ bảng/cột cho phép, `case_code` phải có trong `prompt_cases`, `task_violated` chỉ kèm `judged_by="rule"`, tổng kích thước ≤ `MAX_RESULT_BYTES`. **Một dòng sai là bỏ cả nhóm** (không lưu một nửa); event cuối khi đó có `data.partial_dropped = true`. Hãy kiểm tra mã ca trong `validate` để tránh tình huống này |
| Kết quả cấp experiment | **Không có**: không `lab_results`, không `result_kind`, `result: null`. Các dòng từng phần là dữ liệu thô, không có nhãn, không được tính ASR (chưa đủ lượt) |
| Hủy do hết giờ | Vẫn để `JobCancelled` bay ra (mục 7); chỉ ném `LabRunError("JOB_TIMEOUT", ...)` khi chính service biết mình hết thời gian của một phụ thuộc, không phải khi `ctx.check()` báo hủy |

Chỉ dùng cho phụ thuộc ngoài hỏng. Lỗi lập trình của bạn vẫn là ngoại lệ thường (thành `INTERNAL_ERROR`, để lộ ra khi test), và kết quả sai nhãn vẫn đi qua `result_guard`.

## 7. Thời gian chạy và hủy: `ctx.check()`

Mỗi Lab có giới hạn thời gian (`JOB_TIMEOUT_<LAB>`, mặc định 120 giây, `.env`). Khi hết giờ orchestrator gọi `ctx.cancel()`, và `ctx.check()` sẽ ném `JobCancelled`.

```python
for i, frame in enumerate(frames):
    ctx.check()                 # đầu mỗi vòng lặp dài / trước mỗi lời gọi chậm
    process(frame)
```

- **Bắt buộc** gọi `ctx.check()` trong mọi vòng lặp dài (khung hình, lượt gọi mô hình, request). Không bắt `JobCancelled` (hoặc `except Exception` nuốt nó rồi chạy tiếp): hãy để nó bay ra.
- Python không giết được thread. Service không gọi `check()` vẫn bị đánh dấu `failed` (`JOB_TIMEOUT`) sau ~5 giây ân hạn nhưng **thread của bạn vẫn chạy ngầm** tiêu tốn máy cho tới khi tự xong.
- Gọi mạng/subprocess phải có timeout riêng ngắn hơn timeout job (`httpx` có `connect`/`read` timeout; `subprocess.run([...], timeout=…)`, **không** `shell=True`).
- Timeout thực của Lab bạn: báo TV1 sau khi **đo trên máy thật** (SPEC 10.5) để đặt `JOB_TIMEOUT_<LAB>`.

## 8. Sau khi `run` trả về: bạn sẽ thấy gì

```
POST experiment ──► validate ──(lỗi)──► 422, không tạo gì
        │
        ▼ queued ──► running ──► run() ──► result_guard ──► done            (lab_results.guard_passed=1)
                                    │            └─(sai)──► failed RESULT_REJECTED
                                    ├─ ném ngoại lệ ───────► failed INTERNAL_ERROR
                                    └─ hết giờ ────────────► failed JOB_TIMEOUT
```

- Chỉ **một** job chạy tại một thời điểm; các job khác chờ `queued`. Đừng giả định song song, đừng giữ trạng thái toàn cục giữa hai lần chạy (service là **singleton**, tạo một lần ở `registry.py`; trạng thái riêng của mỗi lần chạy để trong biến cục bộ).
- Khởi động lại máy chủ khi đang chạy → job đó `failed` (`INTERRUPTED`), job đang chờ được xếp lại.
- Muốn thấy toàn bộ lý do một lần bị từ chối: bảng `lab_results`, cột `guard_details` (hoặc `GET /experiments/{id}/events` khi T-09 xong).

## 9. Mẫu tối thiểu (đã kiểm chứng) và cách test

```python
# backend/app/services/labs/demo.py  (ví dụ; Lab thật thay nội dung)
from __future__ import annotations
from typing import Any

from app.config import Settings
from app.services.base import (
    Component, LabResult, LabValidationError, RunContext,
    is_int_in, reject_unknown, require_object,
)


class DemoService:
    code = "demo"

    def validate(self, payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
        payload = require_object(payload)
        errors = reject_unknown(payload, {"steps"})
        steps = payload.get("steps", 3)
        if not is_int_in(steps, 1, 10):
            errors.append({"field": "steps", "message": "Phải là số nguyên 1..10."})
        if errors:
            raise LabValidationError(errors)
        return {"steps": steps}

    def run(self, ctx: RunContext, payload: dict[str, Any]) -> LabResult:
        done = 0
        for _ in range(payload["steps"]):
            ctx.check()
            done += 1
        ctx.emit("info", "demo_steps", f"Đã chạy {done} bước", {"steps": done})
        return LabResult(
            result_kind="simulated",
            summary=f"Mô phỏng {done} bước.",
            components=[Component("demo", "simulated", "chỉ là ví dụ")],
            metrics={"steps_done": done},
            limitations=["Ví dụ minh họa, không có số liệu thật."],
        )
```

Test bằng `pytest`, không cần DB hay server (xem `tests/test_lab_services.py`):

```python
from pathlib import Path
from app.config import Settings
from app.services.base import RunContext
from app.services.guard import run_guard

settings = Settings(_env_file=None, jwt_secret="pytest-secret-0123456789abcdef")

def test_demo_passes_guard():
    svc = DemoService()
    payload = svc.validate({"steps": 2}, settings)
    ctx = RunContext(settings, "exp_test", Path("/tmp/storage"), timeout_seconds=5)
    result = svc.run(ctx, payload)
    report = run_guard(result, "simulated", settings)   # trần của Lab; thử "planned" để thấy bị từ chối
    assert report.passed, report.failed
```

Hãy kiểm thêm: mỗi giá trị sai của `validate` (URL, khóa lạ, ngoài khoảng, sai kiểu, `True` thay cho số), trần số lượng, `run` với `timeout_seconds=-1` (phải ném `JobCancelled`), và kết quả của bạn **bị từ chối** ở trần thấp hơn.

Chạy cả vòng đời qua orchestrator (hàng đợi, ghi DB): copy cách dựng ở `tests/test_orchestrator.py` (hàm `run_one`, `FakeService`).

## 10. Việc riêng của từng người

**TV2/TV3 — Deepfake.** Cấu trúc thư mục bàn giao TV2 → TV3 (SPEC 9.3): `storage/experiments/<experiment_id>/` gồm `metadata.json` (`duration_s, fps, width, height, codec, frame_count, has_audio`), `frames/000001.jpg…`, `audio.wav` (nếu có; MVP không phân tích âm thanh, D-03, ghi vào `limitations`). Video lấy theo `asset_id` (bảng `media_assets`); **không** nhận đường dẫn từ client. Hàm lấy đường dẫn an toàn trong `storage/` thuộc T-13 của TV1 (`core/files.py`), **chưa có**: cho tới lúc đó hãy tự ghép `settings.storage_path` với tên tệp và kiểm `Path.resolve()` nằm trong `storage/`. FFmpeg gọi bằng `subprocess.run([...], timeout=…)`. Chưa có detector được phê duyệt: giữ `detector: not_integrated`, không điểm số, `components` gồm `preprocess` (`real` nếu chạy thật) và `detector` (`planned`), nên `result_kind = "planned"`; Lab trần `simulated` vẫn chấp nhận nhãn thấp hơn. Có detector (MỞ-6, chốt cuối tuần 4): thêm `suspicion_score`, `verdict ∈ {suspected, not_detected, insufficient_data}`, ngưỡng đặt trước và lưu cùng kết quả; **không** dùng khóa `probability`/`accuracy`.

**TV4 — Prompt Injection.** Một experiment chạy cả hai nhánh `defenses` với cùng ca, cùng mô hình, cùng `repeat` (D-07). Gọi Ollama (trên Colab, D-18) **chỉ** tới `settings.ollama_url` kèm `Authorization: Bearer settings.ollama_api_key`: `https` bắt buộc, host thuộc `ollama_allowed_hosts`, `follow_redirects=False`, timeout `ollama_connect_timeout`/`ollama_read_timeout`, không đưa token vào log/event/kết quả, phản hồi mô hình là **dữ liệu không tin cậy** (không thực thi, không dùng làm URL/đường dẫn). Chỉ gửi văn bản tổng hợp trong `prompt_cases` (FR-PI-12). `metrics.model_name`, `metrics.endpoint_kind = "colab"`, và `limitations` nêu mô hình chạy trên dịch vụ ngoài (FR-PI-11). Thành phần `ollama_chat` có `kind="real"` **chỉ khi** có phản hồi thật. ASR chỉ tính khi mọi lượt trong nhánh đã được chấm; còn `None` thì ghi "chưa đủ dữ liệu". Liệt kê cả ca phòng thủ không hiệu quả (`metrics.failed_cases`). Colab ngắt giữa chừng (FR-PI-10): ném `LabRunError("DEP_UNAVAILABLE", ..., {"prompt_runs": rows_da_co})` (mục 6b) để experiment `failed` đúng mã mà không mất các lượt đã chạy; các lượt từng phần không được dùng để tính ASR.

**TV4 — API Security.** Gửi **request HTTP thật** (`httpx`) tới `/sandbox/echo` trên `127.0.0.1` từ worker; đích cố định, không nhận URL (FR-API-04). Đo số gửi/200/429 và độ trễ p50/p95 (FR-API-03); mỗi `429` ghi `ctx.emit("warn", "rate_limited", …)`. Trần `MAX_API_LAB_REQUESTS`. `limitations` luôn nêu Rate Limiting đơn lẻ không chống được mọi dạng DoS/DDoS (FR-API-06). `/sandbox/echo` do TV1 dựng khung (T-12); tham số kịch bản (`window`, `max_requests`) do TV4 chọn.

## 11. Thêm một Lab mới

1. `app/services/catalog.py`: thêm một dòng vào `LAB_CATALOG` (`code`, `name`, `status` là **trần** nhãn, `enabled`).
2. `app/services/labs/<code>.py`: lớp service với `code` khớp, cài `validate`/`run`.
3. `app/services/registry.py`: thêm vào `SERVICES` (test `test_registry_matches_catalog` bảo đảm hai bên cùng tập mã).
4. `app/config.py`: thêm `job_timeout_<code>: float | None = Field(default=None, gt=0)` nếu cần timeout riêng.
5. `python -m app.seed` thêm Lab mới vào CSDL (không ghi đè thay đổi của admin).

## 12. Checklist trước khi mở PR

- [ ] `python -m pytest -q` xanh; có test cho `validate` (đúng + sai) và `run` qua `run_guard`.
- [ ] Không import `app.api`, `app.models`, `app.jobs` trong `services/`; không ghi DB; không `print`.
- [ ] `validate` từ chối khóa lạ, URL, đường dẫn; trả giá trị mặc định đã điền; có trần.
- [ ] `limitations` thật và không rỗng; `result_kind` = nhãn yếu nhất của `components`; không bịa số liệu; không khóa `probability`/`accuracy`/`confidence_pct`.
- [ ] Mọi vòng lặp dài gọi `ctx.check()`; mọi gọi ngoài có timeout; không `shell=True`.
- [ ] Không bí mật trong mã/log/event; `.env` không commit.
- [ ] Nếu cần đổi hợp đồng (`LabResult`, `RunContext`, `records`, mã lỗi) → tạo issue/PR riêng cho TV1 và nêu ảnh hưởng tới Lab khác.

## Phụ lục. Lịch sử phiên bản

| Phiên bản | Ngày | Nội dung |
|---|---|---|
| 0.1 | 19/11/2026 | Bản đầu (T-11): hợp đồng `LabService`, luật G1–G7, `ctx.emit`/`ctx.check`, `records`, timeout, mẫu và checklist, việc riêng từng Lab |
| 0.2 | 20/11/2026 | Thêm `LabRunError` (mục 6b): báo `DEP_UNAVAILABLE`/`JOB_TIMEOUT` và lưu `partial_records` khi phụ thuộc ngoài hỏng giữa chừng; sửa mục 3 và việc riêng TV4 |
