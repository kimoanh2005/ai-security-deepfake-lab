"""Tài liệu hóa lỗi `application/problem+json` (RFC 9457) trong OpenAPI (API-01 mục 4.3).

FastAPI mặc định mô tả lỗi 422 bằng `HTTPValidationError`, còn dạng lỗi thật do `core/errors.py` trả là
`application/problem+json` với `code` ổn định. Module này làm tài liệu khớp với thực tế mà không phải lặp lại
khai báo ở từng endpoint:

* Lỗi suy ra được từ chính route được tự thêm (401/403 từ dependency xác thực, 429 từ dependency giới hạn tần suất,
  404 từ tham số đường dẫn, 400/422 từ thân/tham số truy vấn, 500 cho mọi endpoint).
* Lỗi riêng của một endpoint (409, 413, 415, 503…) khai báo ở decorator bằng `problem_responses({...})`,
  và được ưu tiên hơn phần suy ra.
"""
from __future__ import annotations

import json
from typing import Any, Iterator, Mapping

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from app.core.errors import PROBLEM_JSON
from app.deps import get_current_user, require_admin

API_PREFIX = "/api/v1"
PROBLEM_REF = "#/components/schemas/ProblemDetails"
_LEGACY_VALIDATION_SCHEMAS = ("HTTPValidationError", "ValidationError")


class FieldError(BaseModel):
    field: str = Field(description="Tên trường (đường dẫn dạng a.b) hoặc tên header/tham số truy vấn gây lỗi.")
    message: str


class ProblemDetails(BaseModel):
    """Phản hồi lỗi. Client phân nhánh theo `code`, không theo `title`/`detail` (có thể đổi lời)."""

    type: str = Field(examples=["urn:lab:problem:validation-error"])
    title: str
    status: int
    detail: str
    instance: str | None = Field(None, description="Đường dẫn của yêu cầu gây lỗi.")
    code: str = Field(description="Mã lỗi ổn định, ví dụ VALIDATION_ERROR.", examples=["VALIDATION_ERROR"])
    errors: list[FieldError] | None = Field(None, description="Chi tiết theo trường (chỉ có ở lỗi 4xx về dữ liệu).")


def problem_responses(descriptions: Mapping[int, str]) -> dict[int | str, dict[str, Any]]:
    """Dùng ở `responses=` của decorator: `{409: "LAB_DISABLED: Lab đang tắt."}` → phản hồi problem+json."""
    return {
        status: {"description": text, "content": {PROBLEM_JSON: {"schema": {"$ref": PROBLEM_REF}}}}
        for status, text in descriptions.items()
    }


_IMPLIED = {
    400: "MALFORMED_REQUEST: thân không phải JSON hợp lệ hoặc bị thiếu.",
    401: "UNAUTHENTICATED: thiếu, sai hoặc hết hạn token.",
    403: "FORBIDDEN: chỉ quản trị viên được thực hiện.",
    404: "NOT_FOUND: không có tài nguyên được yêu cầu.",
    422: "VALIDATION_ERROR: dữ liệu hoặc tham số không hợp lệ; `errors[]` chỉ rõ từng trường.",
    429: "RATE_LIMITED: vượt giới hạn tần suất; xem `Retry-After` và `RateLimit-*`.",
    500: "INTERNAL_ERROR: lỗi không lường trước; không lộ chi tiết nội bộ.",
}
_HEADERS = {
    401: {"WWW-Authenticate": {"description": "Luôn là `Bearer`.", "schema": {"type": "string"}}},
    429: {
        "Retry-After": {"description": "Số giây nên chờ trước khi thử lại.", "schema": {"type": "integer"}},
        "RateLimit-Limit": {"schema": {"type": "integer"}},
        "RateLimit-Remaining": {"schema": {"type": "integer"}},
        "RateLimit-Reset": {"schema": {"type": "integer"}},
    },
}


def _walk(dependant: Dependant) -> Iterator[Dependant]:
    yield dependant
    for sub in dependant.dependencies:
        yield from _walk(sub)


def _implied_statuses(route: APIRoute) -> list[int]:
    nodes = list(_walk(route.dependant))
    calls = {node.call for node in nodes}
    statuses = [500]
    if get_current_user in calls:
        statuses.append(401)
    if require_admin in calls:
        statuses.append(403)
    if any(getattr(call, "rate_limited", False) for call in calls):
        statuses.append(429)
    if route.dependant.path_params:
        statuses.append(404)
    if any(node.body_params for node in nodes):
        statuses += [400, 422]
    elif any(node.query_params for node in nodes):
        statuses.append(422)
    return sorted(set(statuses))


def _problem_response(status: int) -> dict[str, Any]:
    response: dict[str, Any] = {
        "description": _IMPLIED[status],
        "content": {PROBLEM_JSON: {"schema": {"$ref": PROBLEM_REF}}},
    }
    if status in _HEADERS:
        response["headers"] = _HEADERS[status]
    return response


def _is_legacy_422(response: Mapping[str, Any]) -> bool:
    schema = response.get("content", {}).get("application/json", {}).get("schema", {})
    return schema.get("$ref", "").endswith("/HTTPValidationError")


def _register_problem_schemas(schema: dict[str, Any]) -> None:
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    model = ProblemDetails.model_json_schema(ref_template="#/components/schemas/{model}")
    schemas.update(model.pop("$defs", {}))
    schemas["ProblemDetails"] = model


def _drop_unused_legacy_schemas(schema: dict[str, Any]) -> None:
    schemas = schema["components"]["schemas"]
    for name in _LEGACY_VALIDATION_SCHEMAS:
        if f'"#/components/schemas/{name}"' not in json.dumps(schema["paths"]):
            schemas.pop(name, None)


def document_errors(schema: dict[str, Any], routes: list[Any]) -> dict[str, Any]:
    """Bổ sung phản hồi lỗi problem+json vào schema OpenAPI (sửa tại chỗ, chạy lại nhiều lần vẫn cho kết quả như nhau)."""
    _register_problem_schemas(schema)
    for route in routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema or not route.path_format.startswith(API_PREFIX):
            continue
        implied = _implied_statuses(route)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            responses = schema["paths"][route.path_format][method.lower()]["responses"]
            if "422" in responses and _is_legacy_422(responses["422"]):
                del responses["422"]
            for status in implied:
                responses.setdefault(str(status), _problem_response(status))
    _drop_unused_legacy_schemas(schema)
    return schema


def install_error_docs(app: FastAPI) -> None:
    """Thay `app.openapi` bằng bản đã ghi lỗi problem+json (kết quả được FastAPI cache như thường lệ)."""
    build_default = app.openapi

    def openapi() -> dict[str, Any]:
        fresh = app.openapi_schema is None
        schema = build_default()
        if fresh:
            document_errors(schema, app.routes)
        return schema

    app.openapi = openapi
