"""ETag mạnh theo nội dung JSON và so khớp If-None-Match (RFC 9110 13.1.2), dùng chung cho các GET có ETag."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def etag_of(body: Any) -> str:
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return '"' + hashlib.sha256(raw).hexdigest()[:32] + '"'


def if_none_match(header: str | None, etag: str) -> bool:
    """So khớp yếu với danh sách ETag hoặc `*`."""
    if not header:
        return False
    if header.strip() == "*":
        return True
    strip = lambda t: t.strip().removeprefix("W/")  # noqa: E731
    return any(strip(tag) == etag for tag in header.split(","))
