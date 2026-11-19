import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config import Settings, get_settings
from app.core.db import make_engine, make_session_factory
from app.core.errors import install_error_handlers
from app.core.ratelimit import SlidingWindowLimiter
from app.jobs.orchestrator import Orchestrator

logging.getLogger("app").setLevel(logging.INFO)

EXPOSED_HEADERS = ["Location", "Retry-After", "ETag", "RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory: test dựng được app với Settings và DB tạm; chạy thật dùng `app` ở cuối tệp."""
    settings = settings or get_settings()
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
    app.state.limiters = {"login": SlidingWindowLimiter(settings.login_rate_limit_per_minute, 60)}

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

    @app.get("/")
    def root():
        return {"message": "AI Security & Deepfake Lab Backend đang hoạt động!"}

    return app


app = create_app()
