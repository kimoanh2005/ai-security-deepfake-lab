"""Kiểm tra địa chỉ Ollama (Colab, SPEC D-18, FR-PI-04) và thăm dò ngắn cho GET /health.

Quy tắc đích: bắt buộc `https`, host phải bằng hoặc là tên con của một mục trong `OLLAMA_ALLOWED_HOSTS`, không
chứa thông tin đăng nhập trong URL. Không theo chuyển hướng. Kết quả trả ra chỉ gồm mã lý do cố định, KHÔNG
bao giờ chứa địa chỉ, token hay nội dung ngoại lệ (thông điệp ngoại lệ mạng có thể chứa địa chỉ đường hầm).
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import Settings

# Mã lý do cố định; đây là tất cả những gì /health được phép nói về lỗi Ollama.
REASON_NOT_CONFIGURED = "not_configured"
REASON_INVALID_URL = "invalid_url"
REASON_UNREACHABLE = "unreachable"
REASON_UNAUTHORIZED = "unauthorized"
REASON_BAD_RESPONSE = "bad_response"
REASON_MODEL_MISSING = "model_missing"

PROBE_TIMEOUT_SECONDS = 2.0
MAX_PROBE_BYTES = 1_048_576  # đọc tối đa 1 MiB danh sách mô hình; phản hồi từ Colab là dữ liệu không tin cậy


def allowed_hosts(settings: Settings) -> list[str]:
    return [h.strip().lower().lstrip(".") for h in settings.ollama_allowed_hosts.split(",") if h.strip()]


def validate_ollama_url(url: str, hosts: list[str]) -> str | None:
    """Trả mã lý do nếu `url` không được phép làm đích Ollama; None nếu hợp lệ."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018 - truy cập để kích hoạt ValueError nếu cổng sai
    except ValueError:
        return REASON_INVALID_URL
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return REASON_INVALID_URL
    if not any(host == h or host.endswith("." + h) for h in hosts):
        return REASON_INVALID_URL
    return None


def probe_ollama(settings: Settings, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Một lần gọi `GET {OLLAMA_URL}/api/tags` với timeout ngắn. Không ném ngoại lệ.

    Trả `{"status": "up"|"down"|"not_configured", "reason": <mã cố định>?}`.
    """
    url = (settings.ollama_url or "").strip()
    if not url:
        return {"status": "not_configured", "reason": REASON_NOT_CONFIGURED}
    if validate_ollama_url(url, allowed_hosts(settings)) is not None:
        return {"status": "down", "reason": REASON_INVALID_URL}

    headers = {"Accept": "application/json"}
    if settings.ollama_api_key:
        headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
    timeout = httpx.Timeout(PROBE_TIMEOUT_SECONDS)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, transport=transport, trust_env=False) as client:
            resp = client.get(url.rstrip("/") + "/api/tags", headers=headers)
            if resp.status_code in (401, 403):
                return {"status": "down", "reason": REASON_UNAUTHORIZED}
            if resp.status_code != 200:
                return {"status": "down", "reason": REASON_BAD_RESPONSE}
            if len(resp.content) > MAX_PROBE_BYTES:
                return {"status": "down", "reason": REASON_BAD_RESPONSE}
            body = resp.json()
    except Exception:  # noqa: BLE001 - mọi lỗi mạng/JSON đều thành "down"; không đưa nội dung ngoại lệ ra ngoài
        return {"status": "down", "reason": REASON_UNREACHABLE}

    if not isinstance(body, dict) or not isinstance(body.get("models"), list):
        return {"status": "down", "reason": REASON_BAD_RESPONSE}
    wanted = (settings.ollama_model or "").strip()
    if wanted:
        names = {m.get("name") for m in body["models"] if isinstance(m, dict)} | {
            m.get("model") for m in body["models"] if isinstance(m, dict)
        }
        if wanted not in names and f"{wanted}:latest" not in names:
            return {"status": "down", "reason": REASON_MODEL_MISSING}
    return {"status": "up"}
