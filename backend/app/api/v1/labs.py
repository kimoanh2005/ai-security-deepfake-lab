"""GET /labs, GET /labs/{lab}, PATCH /labs/{lab} (admin)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.ratelimit import control_limit
from app.core.query import PageParams, collection, reject_unknown_query
from app.deps import get_current_user, get_db, require_admin
from app.models import Lab, User
from app.models.schemas.labs import LabPatch
from app.services.catalog import LEGACY_IDS

router = APIRouter(prefix="/labs", tags=["labs"], dependencies=[Depends(control_limit)])
security_log = logging.getLogger("app.security")


def lab_out(lab: Lab) -> dict[str, Any]:
    # `id` số là trường tạm để không phá frontend hiện tại (deprecated); định danh chính thức là `code`.
    # Chỉ liệt kê `links` của tài nguyên đã có thật; thêm scenarios/cases/experiments khi endpoint tồn tại.
    return {
        "id": LEGACY_IDS.get(lab.code),
        "code": lab.code,
        "name": lab.name,
        "description": lab.description,
        "status": lab.status,
        "enabled": bool(lab.enabled),
        "links": {"self": f"/api/v1/labs/{lab.code}"},
    }


def _get_lab(db: Session, code: str) -> Lab:
    lab = db.scalar(select(Lab).where(Lab.code == code))
    if lab is None:
        raise AppError(404, "NOT_FOUND", "Không tìm thấy Lab.")
    return lab


@router.get("", operation_id="list_labs")
def list_labs(
    request: Request,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    reject_unknown_query(request, {"page", "page_size"})
    total = db.scalar(select(func.count()).select_from(Lab)) or 0
    # Thứ tự ổn định theo danh mục (Lab seed cùng mili-giây nên created_at không đủ phân biệt); Lab ngoài danh mục xếp sau.
    catalog_order = case(LEGACY_IDS, value=Lab.code, else_=len(LEGACY_IDS) + 1)
    rows = db.scalars(
        select(Lab).order_by(catalog_order, Lab.created_at, Lab.code).offset(params.offset).limit(params.page_size)
    )
    return collection([lab_out(r) for r in rows], params, total)


@router.get("/{code}", operation_id="get_lab")
def get_lab(code: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return lab_out(_get_lab(db, code))


@router.patch("/{code}", operation_id="update_lab")
def update_lab(code: str, body: LabPatch, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    lab = _get_lab(db, code)
    changes = body.model_dump(exclude_none=True)
    if "enabled" in changes:
        lab.enabled = 1 if changes["enabled"] else 0
    if "status" in changes:
        lab.status = changes["status"]
    db.commit()
    # Sự kiện bảo mật/vận hành ghi ra log ứng dụng (D-09), không có bảng riêng.
    security_log.info("lab_toggled by=%s lab=%s changes=%s", admin.id, lab.code, changes)
    return lab_out(lab)
