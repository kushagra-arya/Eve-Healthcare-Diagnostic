import os
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from alembic import command
from app.database import Base, build_engine
from tests.conftest import migration_config


def test_fresh_postgresql_migration():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to a PostgreSQL database ending in _test")
    url = make_url(database_url)
    if url.drivername != "postgresql+psycopg" or not (url.database or "").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must use postgresql+psycopg and a database ending in _test")

    engine = build_engine(database_url)
    schema = f"eve_foundation_{uuid4().hex}"
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                command.upgrade(migration_config(connection), "head")
                context = MigrationContext.configure(connection, opts={"compare_type": True})
                assert compare_metadata(context, Base.metadata) == []
                assert set(inspect(connection).get_table_names()) == {
                    "users",
                    "diagnostic_centres",
                    "diagnostic_tests",
                    "centre_tests",
                    "bookings",
                    "payments",
                    "webhook_events",
                    "alembic_version",
                }
                command.downgrade(migration_config(connection), "base")
                assert set(inspect(connection).get_table_names()) <= {"alembic_version"}
                command.upgrade(migration_config(connection), "head")
            finally:
                # PostgreSQL transactional DDL removes only this test's temporary schema.
                transaction.rollback()
    finally:
        engine.dispose()
