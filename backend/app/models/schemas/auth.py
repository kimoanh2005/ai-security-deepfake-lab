from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        # Chuẩn hóa chữ thường như cột users.email. Không dùng EmailStr vì nó từ chối tên miền
        # dành riêng như .local (tài khoản mẫu @lab.local).
        v = v.strip().lower()
        if "@" not in v or v.startswith("@") or v.endswith("@"):
            raise ValueError("email không hợp lệ")
        return v


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    created_at: str
