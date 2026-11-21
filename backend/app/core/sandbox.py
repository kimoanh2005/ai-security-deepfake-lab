"""Trạng thái đếm của /sandbox/echo (T-12, FR-API-01).

Mỗi lần chạy Lab có một khóa chạy (`run`) riêng, và mỗi chế độ có bộ đếm riêng:
- `on`: cửa sổ trượt `max_requests`/`window_seconds`; vượt thì bị chặn (429).
- `off`: không chặn, chỉ đếm.
Bộ đếm sandbox độc lập với `app.state.limiters` (đăng nhập) nên không ảnh hưởng `/api/v1`.
Số khóa chạy được giữ bị chặn (cũ nhất bị loại) để bộ nhớ không phình.
"""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from typing import Callable

MODES = ("on", "off")
MAX_RUNS_TRACKED = 256


class SandboxState:
    def __init__(
        self,
        max_requests: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        # run -> {"on": deque[timestamps], "off": [count]}
        self._runs: OrderedDict[str, dict] = OrderedDict()

    def _slot(self, run: str) -> dict:
        slot = self._runs.get(run)
        if slot is None:
            slot = {"on": deque(), "off": 0}
            self._runs[run] = slot
            while len(self._runs) > MAX_RUNS_TRACKED:
                self._runs.popitem(last=False)
        else:
            self._runs.move_to_end(run)
        return slot

    def hit(self, run: str, mode: str) -> dict:
        """Ghi một request. Trả {allowed, count, remaining, retry_after}.

        `count` là số request đã chấp nhận trong chế độ đó (`on`: trong cửa sổ hiện tại; `off`: từ đầu).
        """
        with self._lock:
            slot = self._slot(run)
            if mode == "off":
                slot["off"] += 1
                return {"allowed": True, "count": slot["off"], "remaining": None, "retry_after": 0}
            q: deque[float] = slot["on"]
            now = self._clock()
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.max_requests:
                return {
                    "allowed": False,
                    "count": len(q),
                    "remaining": 0,
                    "retry_after": max(1, math.ceil(self.window - (now - q[0]))),
                }
            q.append(now)
            return {"allowed": True, "count": len(q), "remaining": self.max_requests - len(q), "retry_after": 0}

    def reset(self) -> None:
        with self._lock:
            self._runs.clear()
