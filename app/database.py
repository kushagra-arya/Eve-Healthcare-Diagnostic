from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import MetaData, create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def build_engine(database_url: str) -> Engine:
    options = {"pool_pre_ping": True}
    if database_url.startswith("sqlite:"):
        options["connect_args"] = {"check_same_thread": False, "timeout": 10}
    else:
        options["connect_args"] = {"connect_timeout": 5}
    engine = create_engine(database_url, **options)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def get_db(request: Request) -> Generator[Session, None, None]:
    with request.app.state.session_factory() as session:
        try:
            # SQLite has no row locks; serialize test writes before any reads.
            if session.get_bind().dialect.name == "sqlite" and request.method in {"POST", "PATCH"}:
                session.execute(text("BEGIN IMMEDIATE"))
            yield session
        except Exception:
            session.rollback()
            raise


DbSession = Annotated[Session, Depends(get_db)]
