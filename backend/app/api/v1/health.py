"""GET /health: công khai, không cần token (FR-AUTH-03, FR-OPS-01)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["system"])


@router.get("/health", operation_id="get_health")
def get_health(request: Request) -> JSONResponse:
    code, body = request.app.state.health.check()
    return JSONResponse(body, status_code=code, headers={"Cache-Control": "no-store"})
