from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.database import build_engine
from app.exceptions import ErrorResponse, register_exception_handlers
from app.rate_limit import RateLimiter, RateLimitMiddleware
from app.request_logging import RequestLoggingMiddleware, configure_logging
from app.routers.auth import router as auth_router
from app.routers.bookings import router as bookings_router
from app.routers.catalogue import router as catalogue_router
from app.routers.health import router as health_router
from app.routers.payments import router as payments_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = build_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="EVE Diagnostics API",
        version="0.1.0",
        lifespan=lifespan,
        description="Diagnostic catalogue, patient bookings, and deterministic simulated payments.",
        responses={
            422: {"model": ErrorResponse, "description": "Invalid request"},
            500: {"model": ErrorResponse, "description": "Unexpected internal failure"},
            503: {"model": ErrorResponse, "description": "Temporary database failure; retry later"},
        },
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.rate_limiter = RateLimiter(settings)
    register_exception_handlers(app)
    app.add_middleware(RateLimitMiddleware, limiter=app.state.rate_limiter)
    app.add_middleware(RequestLoggingMiddleware)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(catalogue_router)
    app.include_router(bookings_router)
    app.include_router(payments_router)
    return app
