"""Ánh xạ code Lab → service. Thêm Lab mới = thêm 1 dòng ở `catalog.LAB_CATALOG`, 1 file service và 1 dòng ở đây.

Danh mục (tên, status, enabled mặc định) nằm ở `app.services.catalog`; module này chỉ gắn service cài đặt.
Có test bảo đảm hai bên luôn cùng tập mã Lab.
"""
from __future__ import annotations

from app.services.base import LabService
from app.services.labs.api_lab import ApiLabService
from app.services.labs.deepfake import DeepfakeService
from app.services.labs.poisoning import PoisoningService
from app.services.labs.prompt_injection import PromptInjectionService

SERVICES: dict[str, LabService] = {
    s.code: s for s in (DeepfakeService(), PromptInjectionService(), ApiLabService(), PoisoningService())
}


def get_service(code: str) -> LabService | None:
    return SERVICES.get(code)
