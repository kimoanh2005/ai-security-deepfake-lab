"""Lab 04 – Data Poisoning: ngoài phạm vi MVP (D-02). Chỉ giữ chỗ trong danh mục, status=planned, enabled=0."""
from __future__ import annotations

from typing import Any

from app.config import Settings
from app.services.base import LabResult, LabValidationError, RunContext


class PoisoningService:
    code = "poisoning"

    def validate(self, payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
        raise LabValidationError([{"field": "", "message": "Lab này chưa được triển khai (planned)."}])

    def run(self, ctx: RunContext, payload: dict[str, Any]) -> LabResult:  # pragma: no cover
        raise NotImplementedError("Lab planned")
