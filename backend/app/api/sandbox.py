"""/sandbox/echo: đối tượng bị thử của Lab API Security (T-12, FR-API-01).

Nằm NGOÀI /api/v1 vì không phải tài nguyên nghiệp vụ. Chỉ Lab (worker) gọi, bằng token nội bộ.
- `X-Lab-Token`: bắt buộc, so sánh hằng thời gian với `settings.sandbox_token`.
- `X-Lab-Rate-Limit: on|off`: bật/tắt giới hạn tần suất cho RIÊNG request này (mặc định `on`).
- `X-Lab-Run`: khóa chạy tùy chọn (tối đa 64 ký tự chữ/số/_/-), tách bộ đếm giữa các lần chạy (mặc định `default`).
Endpoint không bao giờ gọi ra ngoài và không dùng nội dung body làm địa chỉ/đường dẫn; chỉ trả kích thước và tên khóa.
"""
from __future__ import annotations

import hmac
import json
import re
from typing import Any

from fastapi import APIRouter, Request, Response

from app.core.errors import AppError
from app.core.sandbox import MODES

router = APIRouter(prefix="/sandbox", tags=["sandbox"])

MAX_BODY_BYTES = 4096
MAX_KEYS_ECHOED = 20
RUN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _authorize(request: Request) -> None:
    expected = request.app.state.settings.sandbox_token
    given = request.headers.get("x-lab-token", "")
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise AppError(401, "UNAUTHENTICATED", "Thiếu hoặc sai token Lab nội bộ.", headers={"WWW-Authenticate": "Lab"})


def _mode_and_run(request: Request) -> tuple[str, str]:
    mode = request.headers.get("x-lab-rate-limit", "on").strip().lower()
    run = request.headers.get("x-lab-run", "default").strip()
    errors = []
    if mode not in MODES:
        errors.append({"field": "X-Lab-Rate-Limit", "message": "Chỉ nhận 'on' hoặc 'off'."})
    if not RUN_RE.fullmatch(run):
        errors.append({"field": "X-Lab-Run", "message": "Tối đa 64 ký tự chữ/số/gạch."})
    if errors:
        raise AppError(422, "VALIDATION_ERROR", "Header sandbox không hợp lệ.", errors=errors)
    return mode, run


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise AppError(413, "PAYLOAD_TOO_LARGE", f"Thân yêu cầu tối đa {MAX_BODY_BYTES} byte.")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > MAX_BODY_BYTES:
            raise AppError(413, "PAYLOAD_TOO_LARGE", f"Thân yêu cầu tối đa {MAX_BODY_BYTES} byte.")
    return bytes(buf)


def _describe(body: bytes) -> dict[str, Any]:
    """Chỉ mô tả (kích thước, tên khóa cấp trên); không diễn giải, không gọi, không dùng nội dung làm đích."""
    info: dict[str, Any] = {"received_bytes": len(body)}
    if body:
        try:
            data = json.loads(body)
        except ValueError:
            return info
        if isinstance(data, dict):
            info["keys"] = sorted(str(k)[:64] for k in list(data)[:MAX_KEYS_ECHOED])
    return info


def _handle(request: Request, response: Response, body: bytes) -> dict[str, Any]:
    _authorize(request)  # được gọi trước khi đọc/diễn giải thân
    mode, run = _mode_and_run(request)
    state = request.app.state.sandbox
    res = state.hit(run, mode)
    if mode == "on":
        headers = {
            "RateLimit-Limit": str(state.max_requests),
            "RateLimit-Remaining": str(res["remaining"]),
        }
        if not res["allowed"]:
            headers.update({"Retry-After": str(res["retry_after"]), "RateLimit-Reset": str(res["retry_after"])})
            raise AppError(429, "RATE_LIMITED", "Sandbox: vượt giới hạn tần suất.", headers=headers)
        response.headers.update(headers)
    return {
        "ok": True,
        "mode": mode,
        "count": res["count"],
        "method": request.method,
        **_describe(body),
    }


@router.get("/echo", operation_id="sandbox_echo_get")
def echo_get(request: Request, response: Response) -> dict[str, Any]:
    return _handle(request, response, b"")


@router.post("/echo", operation_id="sandbox_echo_post")
async def echo_post(request: Request, response: Response) -> dict[str, Any]:
    _authorize(request)  # từ chối trước khi đọc thân
    return _handle(request, response, await _read_body(request))
