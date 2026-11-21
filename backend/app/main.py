import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import sandbox as sandbox_api
from app.api.v1.router import api_router
from app.config import Settings, get_settings
from app.core.db import make_engine, make_session_factory
from app.core.errors import install_error_handlers
from app.core.health import HealthChecker
from app.core.ratelimit import SlidingWindowLimiter
from app.core.sandbox import SandboxState
from app.jobs.orchestrator import Orchestrator

logging.getLogger("app").setLevel(logging.INFO)
# httpx ghi URL đầy đủ mỗi lời gọi ở mức INFO; với Ollama đó là địa chỉ đường hầm, không được vào log.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

EXPOSED_HEADERS = ["Location", "Retry-After", "ETag", "RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory: test dựng được app với Settings và DB tạm; chạy thật dùng `app` ở cuối tệp."""
    settings = settings or get_settings()
    if not settings.sandbox_token:
        settings.sandbox_token = secrets.token_urlsafe(32)  # mỗi lần khởi động; Lab đọc từ ctx.settings, không ghi log
    engine = make_engine(settings.db_file)

    session_factory = make_session_factory(engine)
    orchestrator = Orchestrator(settings, session_factory)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        settings.storage_path.mkdir(parents=True, exist_ok=True)
        orchestrator.start()  # khôi phục job treo (FR-EXP-04) rồi chạy worker; cần CSDL đã migrate
        yield
        orchestrator.stop()
        engine.dispose()

    app = FastAPI(title="AI Security & Deepfake Lab API", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.orchestrator = orchestrator
    app.state.sandbox = SandboxState(settings.sandbox_max_requests, settings.sandbox_window_seconds)
    app.state.health = HealthChecker(settings, session_factory, orchestrator, app.version)
    app.state.limiters = {
        "login": SlidingWindowLimiter(settings.login_rate_limit_per_minute, 60),
        "control": SlidingWindowLimiter(settings.api_rate_limit_per_minute, 60),
        "heavy": SlidingWindowLimiter(settings.heavy_rate_limit_per_minute, 60),
    }

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "If-None-Match", "If-Match", "Idempotency-Key"],
        expose_headers=EXPOSED_HEADERS,
    )

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    install_error_handlers(app)
    app.include_router(api_router)
    app.include_router(sandbox_api.router)  # ngoài /api/v1: đối tượng bị thử, không phải tài nguyên nghiệp vụ

    @app.get("/")
    def root():
        return {"message": "AI Security & Deepfake Lab Backend đang hoạt động!"}

    return app


app = create_app()
