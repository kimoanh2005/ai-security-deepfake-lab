"""Gom trạng thái cho GET /api/v1/health (FR-OPS-01).

- DB: `SELECT 1`. Worker: `orchestrator.health()`. FFmpeg: tìm trong PATH (không chạy tiến trình, không lộ đường dẫn).
- Ollama (Colab): thăm dò qua mạng, timeout ngắn, kết quả nhớ ~30 s để nhiều lời gọi /health không dồn lên Colab.
- Đầu ra chỉ gồm trạng thái và mã lý do cố định. Không có địa chỉ, token, đường dẫn hay nội dung ngoại lệ.
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import text

from app.config import Settings
from app.core.ollama import probe_ollama

log = logging.getLogger("app.health")

OLLAMA_CACHE_SECONDS = 30.0


class HealthChecker:
    def __init__(
        self,
        settings: Settings,
        session_factory: Callable[[], Any],
        orchestrator: Any,
        version: str,
        probe: Callable[[Settings], dict[str, Any]] = probe_ollama,
        clock: Callable[[], float] = time.monotonic,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._orchestrator = orchestrator
        self._version = version
        self._probe = probe
        self._clock = clock
        self._which = which
        self._lock = threading.Lock()
        self._ollama: dict[str, Any] | None = None
        self._ollama_at = 0.0

    def _database(self) -> dict[str, Any]:
        try:
            with self._session_factory() as db:
                db.execute(text("SELECT 1"))
            return {"status": "up"}
        except Exception:  # noqa: BLE001
            log.warning("health: kiểm tra CSDL lỗi", exc_info=True)
            return {"status": "down"}

    def _worker(self) -> dict[str, Any]:
        try:
            h = self._orchestrator.health()
        except Exception:  # noqa: BLE001
            log.warning("health: kiểm tra worker lỗi", exc_info=True)
            return {"status": "down"}
        return {"status": "up" if h.get("worker_alive") else "down", "queue_size": h.get("queue_size", 0), "busy": bool(h.get("busy"))}

    def _ffmpeg(self) -> dict[str, Any]:
        return {"status": "up" if self._which("ffmpeg") else "down"}

    def _ollama_status(self) -> dict[str, Any]:
        now = self._clock()
        with self._lock:  # một lần thăm dò tại một thời điểm; lời gọi đến sau dùng kết quả vừa có
            if self._ollama is None or now - self._ollama_at >= OLLAMA_CACHE_SECONDS:
                try:
                    result = self._probe(self._settings)
                except Exception:  # noqa: BLE001 - probe không được làm sập /health
                    log.warning("health: thăm dò Ollama lỗi", exc_info=True)
                    result = {"status": "down", "reason": "unreachable"}
                self._ollama = {k: v for k, v in result.items() if k in ("status", "reason")}
                self._ollama_at = self._clock()
            return dict(self._ollama)

    def check(self) -> tuple[int, dict[str, Any]]:
        """Trả (mã HTTP, thân). 503 chỉ khi lõi (CSDL, worker) hỏng; Ollama/FFmpeg hỏng chỉ làm `degraded`."""
        components = {
            "database": self._database(),
            "worker": self._worker(),
            "ffmpeg": self._ffmpeg(),
            "ollama": self._ollama_status(),
        }
        core_ok = components["database"]["status"] == "up" and components["worker"]["status"] == "up"
        all_up = all(c["status"] == "up" for c in components.values())
        status = "ok" if all_up else ("degraded" if core_ok else "down")
        body = {
            "status": status,
            "version": self._version,
            "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "components": components,
        }
        return (200 if core_ok else 503), body
