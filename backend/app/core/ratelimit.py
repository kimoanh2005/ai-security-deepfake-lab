"""Giới hạn tần suất theo cửa sổ trượt, trong bộ nhớ (đủ cho 1 tiến trình / demo cục bộ)."""
from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from typing import Callable

from fastapi import Request, Response

from app.core.errors import AppError


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int = 60, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> tuple[bool, int, int]:
        """Trả về (được_phép, còn_lại, giây_chờ_nếu_bị_chặn)."""
        now = self._clock()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False, 0, max(1, math.ceil(self.window - (now - q[0])))
            q.append(now)
            return True, self.limit - len(q), 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def mask_ip(ip: str) -> str:
    """Che một phần IP khi ghi log (SPEC 10.3)."""
    if ":" in ip:  # IPv6
        parts = ip.split(":")
        return ":".join(parts[:2]) + ":*"
    parts = ip.split(".")
    return ".".join(parts[:2]) + ".*.*" if len(parts) == 4 else "unknown"


def rate_limit(name: str):
    """Dependency: dùng limiter `name` trong app.state.limiters."""

    def dependency(request: Request, response: Response) -> None:
        limiter: SlidingWindowLimiter = request.app.state.limiters[name]
        ok, remaining, retry = limiter.hit(client_ip(request))
        if not ok:
            raise AppError(
                429,
                "RATE_LIMITED",
                "Quá nhiều yêu cầu. Vui lòng thử lại sau.",
                headers={
                    "Retry-After": str(retry),
                    "RateLimit-Limit": str(limiter.limit),
                    "RateLimit-Remaining": "0",
                    "RateLimit-Reset": str(retry),
                },
            )
        response.headers["RateLimit-Limit"] = str(limiter.limit)
        response.headers["RateLimit-Remaining"] = str(remaining)

    return dependency
