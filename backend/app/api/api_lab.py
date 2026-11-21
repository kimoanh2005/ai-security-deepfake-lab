import csv
import io
import json
from typing import Literal
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from app.services.security_service import security_service
from app.services.lab_service import log_security_event

router = APIRouter(prefix="/api/security", tags=["API Security & Rate Limiting Lab"])


class RateLimitTestRequest(BaseModel):
    client_name: str = Field("Test Client", max_length=100)
    action: str = Field("ping", max_length=100)
    use_defense: bool = True


@router.get("/logs")
def logs(event_type: str = "", action: str = ""):
    return [entry for entry in security_service.security_logs
            if (not event_type or entry["event_type"] == event_type) and (not action or entry["action_taken"] == action)]


@router.delete("/logs")
def clear_logs():
    security_service.clear_logs()
    return {"message": "Đã xóa nhật ký."}


@router.get("/history")
def history(kind: Literal["comparison", "test", "rate"] = "comparison"):
    return security_service.list_events(kind)


@router.get("/export")
def export(kind: Literal["log", "comparison", "test", "rate"] = "test", format: Literal["json", "csv"] = "json"):
    rows = security_service.list_events(kind, 10000)
    if format == "json":
        return Response(json.dumps(rows, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{kind}.json"'})
    stream = io.StringIO()
    fields = sorted({key for row in rows for key in row})
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        safe = {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for key, value in row.items()}
        # Prevent spreadsheet formula execution when exported text is opened in Excel.
        safe = {key: "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")) else value for key, value in safe.items()}
        writer.writerow(safe)
    return Response("\ufeff" + stream.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'})


@router.post("/rate-limit-test")
def rate_test(payload: RateLimitTestRequest, request: Request):
    ip = request.client.host if request.client else "local"
    allowed, remaining, reset = security_service.check_rate_limit(ip) if payload.use_defense else (True, None, 0)
    headers = {"X-RateLimit-Limit": "5", "X-RateLimit-Remaining": str(remaining if remaining is not None else "unlimited"),
               "X-RateLimit-Reset": str(reset)}
    if not allowed:
        headers["Retry-After"] = str(reset)
    result = {"status": "allowed" if allowed else "limited", "defense_active": payload.use_defense,
              "remaining_quota": remaining, "retry_after_seconds": reset if not allowed else 0, "window_seconds": 30}
    security_service.save("rate", {**result, "http_status": 200 if allowed else 429, "client_ip": ip})
    log_security_event(event_type="API_REQUEST" if allowed else "RATE_LIMIT_EXCEEDED", client_ip=ip,
                              prompt=payload.action, defense_applied=payload.use_defense,
                              action_taken="ALLOWED" if allowed else "HTTP_429_BLOCKED")
    return JSONResponse(result, status_code=200 if allowed else 429, headers=headers)
