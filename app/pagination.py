from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session


class Page[T](BaseModel):
    items: list[T]
    page: int
    page_size: int
    total: int


class Pagination(BaseModel):
    page: int = Field(default=1, ge=1, le=1_000_000)
    page_size: int = Field(default=20, ge=1, le=100)


PageQuery = Annotated[Pagination, Query()]


def paginate(session: Session, query, pagination: Pagination) -> dict:
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    items = session.scalars(
        query.limit(pagination.page_size).offset((pagination.page - 1) * pagination.page_size)
    ).all()
    return {
        "items": items,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total": total,
    }
