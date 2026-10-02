from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import DbSession
from app.exceptions import AppError

router = APIRouter(tags=["health"])


@router.get("/health")
def health(session: DbSession) -> dict[str, str]:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise AppError(503, "database_unavailable", "Database unavailable") from exc
    return {"status": "ok"}
