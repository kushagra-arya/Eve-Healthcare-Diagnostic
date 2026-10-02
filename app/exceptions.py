import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg import OperationalError as DriverOperationalError
from pydantic import BaseModel, Field
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException

logger = logging.getLogger("eve.errors")


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict[str, Any]] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorDetail
    request_id: str | None


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    response_headers = dict(headers or {})
    if request_id:
        response_headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=status,
        content={
            "error": {"code": code, "message": message, "details": details or []},
            "request_id": request_id,
        },
        headers=response_headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def application_error(request: Request, exc: AppError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return error_response(request, exc.status_code, exc.code, exc.message, headers=headers)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error_response(
            request,
            exc.status_code,
            f"http_{exc.status_code}",
            str(exc.detail),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Pydantic's raw errors can include password input and validator context.
        details = [
            {"field": ".".join(map(str, error["loc"])), "type": error["type"]}
            for error in exc.errors()
        ]
        return error_response(request, 422, "validation_error", "Invalid request", details)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        logger.error("database_error", extra={"fields": {"exception_type": type(exc).__name__}})
        sqlstate = (getattr(exc.orig, "sqlstate", "") or "") if isinstance(exc, DBAPIError) else ""
        if isinstance(exc, PoolTimeoutError) or (
            isinstance(exc, DBAPIError)
            and (
                exc.connection_invalidated
                or (isinstance(exc.orig, DriverOperationalError) and not sqlstate)
                or sqlstate in {"40001", "40P01"}
                or sqlstate.startswith("08")
            )
        ):
            return error_response(
                request,
                503,
                "database_unavailable",
                "Database temporarily unavailable",
                headers={"Retry-After": "1"},
            )
        return error_response(request, 500, "internal_error", "An internal error occurred")

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.error(
            "unexpected_error",
            extra={
                "fields": {
                    "exception_type": type(exc).__name__,
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )
        return error_response(request, 500, "internal_error", "An internal error occurred")
