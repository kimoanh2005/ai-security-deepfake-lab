"""Tham số truy vấn dùng chung: phân trang và từ chối tham số lạ (API-01 mục 7)."""
from __future__ import annotations

from typing import Any

from fastapi import Query, Request

from app.core.errors import AppError


def reject_unknown_query(request: Request, allowed: set[str]) -> None:
    unknown = sorted(set(request.query_params) - allowed)
    if unknown:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "Tham số truy vấn không được hỗ trợ.",
            errors=[{"field": k, "message": "Tham số không được hỗ trợ."} for k in unknown],
        )


class PageParams:
    def __init__(self, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def collection(items: list[Any], params: PageParams, total: int) -> dict[str, Any]:
    return {"items": items, "page": params.page, "page_size": params.page_size, "total": total}
