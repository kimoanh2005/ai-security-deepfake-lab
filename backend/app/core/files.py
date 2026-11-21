"""Tải tệp an toàn (T-13, FR-DF-01/02/08): nhận diện theo nội dung, trần dung lượng, tên ngẫu nhiên, đường dẫn trong storage/.

Nguyên tắc:
- Tên tệp client gửi KHÔNG bao giờ được dùng để ghi đĩa; tên lưu là chuỗi ngẫu nhiên + đuôi suy ra từ nội dung.
- Loại tệp xác định bằng magic bytes, không tin đuôi hay Content-Type; ffprobe chỉ chạy sau khi magic bytes hợp lệ.
- Gọi ffprobe bằng danh sách đối số (không `shell=True`), có timeout.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.core.errors import AppError

log = logging.getLogger("app.files")

CHUNK = 1024 * 1024
INCOMING_DIR = ".incoming"  # thư mục tạm trong storage/, cùng ổ đĩa để os.replace nguyên tử

# mime -> đuôi lưu trên đĩa
EXTENSIONS = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm", "video/x-msvideo": ".avi"}


@dataclass(frozen=True)
class StoredUpload:
    path: Path        # tệp tạm đã ghi xong
    size: int
    sha256: str
    mime: str


@dataclass(frozen=True)
class VideoInfo:
    duration_s: float
    fps: float | None
    width: int | None
    height: int | None
    codec: str | None


def sniff_mime(head: bytes) -> str | None:
    """Loại video theo magic bytes; None nếu không thuộc danh sách cho phép."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "video/quicktime" if head[8:12] == b"qt  " else "video/mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":  # EBML: WebM hoặc Matroska
        return "video/webm" if b"webm" in head[:64] else None  # Matroska thường không nhận
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "video/x-msvideo"
    return None


def safe_storage_path(storage: Path, name: str) -> Path:
    """Ghép `name` vào storage/ và bảo đảm kết quả nằm trong storage/. Từ chối tên có phân cách, `..`, tuyệt đối."""
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name or Path(name).is_absolute():
        raise AppError(400, "MALFORMED_REQUEST", "Tên tệp không hợp lệ.")
    root = storage.resolve()
    target = (root / name).resolve()
    if not target.is_relative_to(root) or target == root:
        raise AppError(400, "MALFORMED_REQUEST", "Tên tệp không hợp lệ.")
    return target


def reject(status: int, code: str, detail: str, *, reason: str, user_id: str | None = None, **extra) -> AppError:
    """Ghi `file_rejected` vào log ứng dụng (không chứa bí mật, không chứa tên tệp client) và trả lỗi để raise."""
    log.warning("file_rejected reason=%s user=%s %s", reason, user_id, " ".join(f"{k}={v}" for k, v in extra.items()))
    return AppError(status, code, detail)


def receive_upload(stream: BinaryIO, storage: Path, max_bytes: int, *, user_id: str | None = None) -> StoredUpload:
    """Ghi luồng vào tệp tạm, đếm dung lượng, tính sha256, kiểm magic bytes. Lỗi → xóa tệp tạm."""
    incoming = storage / INCOMING_DIR
    incoming.mkdir(parents=True, exist_ok=True)
    tmp = safe_storage_path(incoming, secrets.token_hex(16) + ".part")
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with open(tmp, "wb") as out:
            while chunk := stream.read(CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise reject(413, "FILE_TOO_LARGE", f"Tệp vượt quá {max_bytes // (1024 * 1024)} MB.",
                                 reason="too_large", user_id=user_id, limit=max_bytes)
                if len(head) < 64:
                    head += chunk[: 64 - len(head)]
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise reject(422, "VALIDATION_ERROR", "Tệp rỗng.", reason="empty", user_id=user_id)
        mime = sniff_mime(head)
        if mime is None:
            raise reject(415, "UNSUPPORTED_MEDIA", "Chỉ nhận video MP4, MOV hoặc WebM (xác định theo nội dung tệp).",
                         reason="bad_magic", user_id=user_id, size=size)
        return StoredUpload(tmp, size, digest.hexdigest(), mime)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def probe_video(path: Path, *, timeout: float, max_seconds: float, user_id: str | None = None) -> VideoInfo:
    """Đọc siêu dữ liệu bằng ffprobe; tệp hỏng, không có luồng video hoặc quá dài bị từ chối."""
    exe = shutil.which("ffprobe")
    if exe is None:
        raise AppError(503, "DEP_UNAVAILABLE", "FFprobe chưa được cài trên máy chủ.", headers={"Retry-After": "30"})
    cmd = [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise reject(415, "UNSUPPORTED_MEDIA", "Không đọc được tệp video.", reason="probe_timeout", user_id=user_id)
    if proc.returncode != 0:
        raise reject(415, "UNSUPPORTED_MEDIA", "Không đọc được tệp video.", reason="probe_failed", user_id=user_id)
    try:
        data = json.loads(proc.stdout or b"{}")
    except ValueError:
        raise reject(415, "UNSUPPORTED_MEDIA", "Không đọc được tệp video.", reason="probe_bad_json", user_id=user_id)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise reject(415, "UNSUPPORTED_MEDIA", "Tệp không có luồng video.", reason="no_video_stream", user_id=user_id)
    duration = _num(data.get("format", {}).get("duration")) or _num(video.get("duration"))
    if duration is None or not math.isfinite(duration) or duration <= 0:
        raise reject(415, "UNSUPPORTED_MEDIA", "Không xác định được độ dài video.", reason="no_duration", user_id=user_id)
    if duration > max_seconds:
        raise reject(422, "VALIDATION_ERROR", f"Video dài tối đa {max_seconds:g} giây.", reason="too_long",
                     user_id=user_id, seconds=round(duration, 1))
    return VideoInfo(
        duration_s=round(duration, 3),
        fps=_fps(video.get("avg_frame_rate")) or _fps(video.get("r_frame_rate")),
        width=_int(video.get("width")),
        height=_int(video.get("height")),
        codec=video.get("codec_name") if isinstance(video.get("codec_name"), str) else None,
    )


def commit_upload(upload: StoredUpload, storage: Path) -> str:
    """Chuyển tệp tạm thành tên ngẫu nhiên cuối cùng trong storage/; trả tên tương đối để lưu vào `file_path`."""
    name = secrets.token_hex(16) + EXTENSIONS[upload.mime]
    os.replace(upload.path, safe_storage_path(storage, name))
    return name


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None


def _fps(v) -> float | None:
    try:
        num, den = str(v).split("/")
        num, den = float(num), float(den)
        return round(num / den, 3) if den else None
    except (ValueError, AttributeError):
        return None
