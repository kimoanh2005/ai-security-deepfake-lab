from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]  # thư mục backend/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    jwt_secret: str                      # bắt buộc, thiếu là báo lỗi ngay
    jwt_expire_minutes: int = 60
    db_path: str = "./storage/lab.db"
    storage_dir: str = "./storage"
    cors_origins: str = "http://localhost:5173"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""

    @property
    def db_file(self) -> Path:
        return (BASE_DIR / self.db_path).resolve()

    @property
    def storage_path(self) -> Path:
        return (BASE_DIR / self.storage_dir).resolve()

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()