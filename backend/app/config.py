from functools import lru_cache
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]  # thư mục backend/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    jwt_secret: str = Field(min_length=16)  # bắt buộc, thiếu hoặc quá ngắn là báo lỗi ngay
    jwt_expire_minutes: int = Field(default=60, ge=1)
    login_rate_limit_per_minute: int = Field(default=10, ge=1)  # số lần thử đăng nhập/phút/IP
    db_path: str = "./storage/lab.db"
    storage_dir: str = "./storage"
    cors_origins: str = "http://localhost:5173"
    # Ollama chạy trên Google Colab (SPEC D-18): địa chỉ đường hầm HTTPS, đổi mỗi phiên Colab, không commit.
    ollama_url: str = ""
    ollama_api_key: str = ""             # token gửi kèm lời gọi; không ghi log
    ollama_allowed_hosts: str = "trycloudflare.com,ngrok-free.app"
    ollama_model: str = ""
    ollama_connect_timeout: float = 5.0
    ollama_read_timeout: float = 120.0

    @field_validator("jwt_secret")
    @classmethod
    def _reject_placeholder_secret(cls, v: str) -> str:
        if v.lower().startswith(("change", "your", "secret", "replace", "<")):
            raise ValueError("JWT_SECRET đang là giá trị mẫu; hãy đặt chuỗi ngẫu nhiên dài (vd: python -c \"import secrets;print(secrets.token_urlsafe(48))\")")
        return v

    @property
    def db_file(self) -> Path:
        return (BASE_DIR / self.db_path).resolve()

    @property
    def storage_path(self) -> Path:
        return (BASE_DIR / self.storage_dir).resolve()

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Đọc cấu hình lười (lần gọi đầu), để test dựng Settings riêng mà không cần .env."""
    return Settings()
