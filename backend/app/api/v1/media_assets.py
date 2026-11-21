"""T-13: POST/GET /media-assets, GET /media-assets/{id} (ETag), GET .../content, DELETE (admin). FR-DF-01, FR-DF-02, FR-DF-08."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.core import files
from app.core.errors import AppError
from app.core.etag import etag_of, if_none_match
from app.core.query import PageParams, collection, reject_unknown_query
from app.deps import get_current_user, get_db, get_settings_dep, require_admin
from app.models import MediaAsset, User

router = APIRouter(prefix="/media-assets", tags=["media-assets"])
security_log = logging.getLogger("app.security")

LABELS = ("original", "synthetic")
ORIGINS = ("sample", "upload")
MAX_TEXT = 500
MULTIPART_OVERHEAD = 64 * 1024  # biên cho các trường văn bản và ranh giới multipart khi so Content-Length


def media_out(m: MediaAsset) -> dict[str, Any]:
    meta = m.meta_json or {}
    return {
        "id": m.id,
        "origin": m.origin,
        "label": m.label,
        "source": m.source,
        "consent_ref": m.consent_ref,
        "technique": m.technique,
        "mime": m.mime_type,
        "size_bytes": m.file_size,
        "sha256": m.sha256,
        "duration_s": meta.get("duration_s"),
        "fps": meta.get("fps"),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "codec": meta.get("codec"),
        "created_at": m.created_at,
        "links": {"self": f"/api/v1/media-assets/{m.id}", "content": f"/api/v1/media-assets/{m.id}/content"},
    }


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _get(db: Session, media_id: str) -> MediaAsset:
    m = db.get(MediaAsset, media_id)
    if m is None:
        raise AppError(404, "NOT_FOUND", "Không tìm thấy tệp media.")
    return m


@router.post("", status_code=201, operation_id="create_media_asset")
def create_media_asset(
    request: Request,
    response: Response,
    file: UploadFile | None = File(None),
    label: str | None = Form(None),
    source: str | None = Form(None),
    consent_ref: str | None = Form(None),
    technique: str | None = Form(None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    user: User = Depends(get_current_user),
):
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise AppError(400, "MALFORMED_REQUEST", "Cần gửi multipart/form-data.")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes + MULTIPART_OVERHEAD:
        raise files.reject(413, "FILE_TOO_LARGE", f"Tệp vượt quá {settings.max_upload_mb} MB.",
                           reason="too_large_declared", user_id=user.id)

    source, consent_ref, technique, label = _clean(source), _clean(consent_ref), _clean(technique), _clean(label)
    if consent_ref is None:
        raise AppError(422, "MISSING_CONSENT", "Thiếu consent_ref: mọi mẫu phải có căn cứ cho phép sử dụng.",
                       errors=[{"field": "consent_ref", "message": "Bắt buộc."}])
    errors = []
    if label not in LABELS:
        errors.append({"field": "label", "message": "Phải là original hoặc synthetic."})
    if source is None:
        errors.append({"field": "source", "message": "Bắt buộc."})
    for name, val in (("source", source), ("consent_ref", consent_ref), ("technique", technique)):
        if val is not None and len(val) > MAX_TEXT:
            errors.append({"field": name, "message": f"Tối đa {MAX_TEXT} ký tự."})
    if file is None or not file.filename:
        errors.append({"field": "file", "message": "Bắt buộc."})
    if errors:
        raise AppError(422, "VALIDATION_ERROR", "Dữ liệu gửi lên không hợp lệ.", errors=errors)

    storage = settings.storage_path
    storage.mkdir(parents=True, exist_ok=True)
    upload = files.receive_upload(file.file, storage, max_bytes, user_id=user.id)
    final_name = None
    try:
        info = files.probe_video(upload.path, timeout=settings.ffprobe_timeout,
                                 max_seconds=settings.max_video_seconds, user_id=user.id)
        final_name = files.commit_upload(upload, storage)
        asset = MediaAsset(
            uploaded_by=user.id, origin="upload", label=label, file_path=final_name, sha256=upload.sha256,
            mime_type=upload.mime, file_size=upload.size, source=source, consent_ref=consent_ref, technique=technique,
            meta_json={"duration_s": info.duration_s, "fps": info.fps, "width": info.width,
                       "height": info.height, "codec": info.codec},
        )
        db.add(asset)
        db.commit()
    except BaseException:
        upload.path.unlink(missing_ok=True)
        if final_name:
            files.safe_storage_path(storage, final_name).unlink(missing_ok=True)
        raise
    security_log.info("media_uploaded by=%s asset=%s size=%s mime=%s", user.id, asset.id, upload.size, upload.mime)
    response.headers["Location"] = f"/api/v1/media-assets/{asset.id}"
    return media_out(asset)


@router.get("", operation_id="list_media_assets")
def list_media_assets(
    request: Request,
    params: PageParams = Depends(),
    origin: str | None = None,
    label: str | None = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    reject_unknown_query(request, {"page", "page_size", "origin", "label"})
    errors = []
    if origin is not None and origin not in ORIGINS:
        errors.append({"field": "origin", "message": "Phải là sample hoặc upload."})
    if label is not None and label not in LABELS:
        errors.append({"field": "label", "message": "Phải là original hoặc synthetic."})
    if errors:
        raise AppError(422, "VALIDATION_ERROR", "Tham số truy vấn không hợp lệ.", errors=errors)
    where = []
    if origin:
        where.append(MediaAsset.origin == origin)
    if label:
        where.append(MediaAsset.label == label)
    total = db.scalar(select(func.count()).select_from(MediaAsset).where(*where)) or 0
    rows = db.scalars(select(MediaAsset).where(*where).order_by(MediaAsset.created_at.desc(), MediaAsset.id)
                      .offset(params.offset).limit(params.page_size))
    return collection([media_out(r) for r in rows], params, total)


@router.get("/{media_id}", operation_id="get_media_asset")
def get_media_asset(media_id: str, request: Request, response: Response, db: Session = Depends(get_db),
                    _user: User = Depends(get_current_user)):
    body = media_out(_get(db, media_id))
    etag = etag_of(body)
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if if_none_match(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return body


@router.get("/{media_id}/content", operation_id="download_media_asset")
def download_media_asset(media_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep),
                         _user: User = Depends(get_current_user)):
    m = _get(db, media_id)
    path = files.safe_storage_path(settings.storage_path, m.file_path)
    if not path.is_file():
        files.log.error("media_file_missing asset=%s", m.id)
        raise AppError(404, "NOT_FOUND", "Không tìm thấy nội dung tệp.")
    ext = path.suffix
    return FileResponse(path, media_type=m.mime_type, filename=f"{m.id}{ext}", content_disposition_type="attachment")


@router.delete("/{media_id}", status_code=204, operation_id="delete_media_asset")
def delete_media_asset(media_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep),
                       admin: User = Depends(require_admin)):
    m = _get(db, media_id)
    if m.origin == "sample":
        raise AppError(409, "STATE_CONFLICT", "Không xóa được mẫu có sẵn.")
    path = files.safe_storage_path(settings.storage_path, m.file_path)
    db.delete(m)
    db.commit()
    path.unlink(missing_ok=True)
    security_log.info("media_deleted by=%s asset=%s", admin.id, media_id)
    return Response(status_code=204)
