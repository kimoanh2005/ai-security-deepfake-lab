"""Lỗi theo RFC 9457 (application/problem+json) với `code` ổn định cho frontend (API-01 mục 4.3)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app.errors")

PROBLEM_JSON = "application/problem+json"

_TITLES = {
    400: "Yêu cầu không hợp lệ",
    401: "Chưa xác thực",
    403: "Không đủ quyền",
    404: "Không tìm thấy",
    405: "Phương thức không được hỗ trợ",
    406: "Định dạng không được hỗ trợ",
    409: "Xung đột trạng thái",
    422: "Dữ liệu không hợp lệ",
    429: "Quá nhiều yêu cầu",
    500: "Lỗi nội bộ",
    503: "Dịch vụ chưa sẵn sàng",
}

_DEFAULT_CODES = {
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    406: "NOT_ACCEPTABLE",
}


class AppError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        *,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.errors = errors
        self.headers = headers or {}


def problem(
    status: int,
    code: str,
    detail: str,
    *,
    request: Request | None = None,
    errors: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"urn:lab:problem:{code.lower().replace('_', '-')}",
        "title": _TITLES.get(status, "Lỗi"),
        "status": status,
        "detail": detail,
    }
    if request is not None:
        body["instance"] = request.url.path
    body["code"] = code
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def _loc_to_field(loc: tuple) -> str:
    return ".".join(str(p) for p in loc if p not in ("body", "query", "path"))


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return problem(exc.status, exc.code, exc.detail, request=request, errors=exc.errors, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        details = exc.errors()
        # JSON hỏng cú pháp → 400; sai schema/khoảng giá trị → 422. Không đưa giá trị đầu vào vào phản hồi
        # (có thể chứa mật khẩu).
        if any(d.get("type") == "json_invalid" for d in details):
            return problem(400, "MALFORMED_REQUEST", "Nội dung yêu cầu không phải JSON hợp lệ.", request=request)
        errors = [{"field": _loc_to_field(d["loc"]), "message": d["msg"]} for d in details]
        return problem(422, "VALIDATION_ERROR", "Dữ liệu gửi lên không hợp lệ.", request=request, errors=errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = _DEFAULT_CODES.get(exc.status_code, "HTTP_ERROR")
        detail = _TITLES.get(exc.status_code, "Lỗi")
        return problem(exc.status_code, code, detail, request=request, headers=dict(exc.headers or {}))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # Chi tiết chỉ ghi log, không trả cho client (không lộ stack trace).
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return problem(500, "INTERNAL_ERROR", "Lỗi nội bộ. Vui lòng thử lại sau.", request=request)
