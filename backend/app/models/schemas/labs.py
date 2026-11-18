from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class LabPatch(BaseModel):
    """Chỉ admin; chỉ các trường được phép đổi (SPEC FR-LAB-03)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["real", "simulated", "planned"] | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _not_empty(self):
        if self.status is None and self.enabled is None:
            raise ValueError("Cần ít nhất một trường: status hoặc enabled")
        return self
