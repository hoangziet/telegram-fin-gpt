"""PostgreSQL integration checks for the restore verifier's read-only mode."""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from fingpt.persistence import Base, User
from fingpt.restore_verification import (
    verify_application_readiness,
    verify_restored_database,
    verify_synthetic_canary_record,
)

DATABASE_URL = os.environ.get("FINGPT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="FINGPT_TEST_DATABASE_URL is not configured")


@pytest.fixture
def postgres_restore_engine() -> Iterator[tuple[Engine, list[str]]]:
    if DATABASE_URL is None:
        pytest.skip("FINGPT_TEST_DATABASE_URL is not configured")

    schema = f"restore_test_{uuid4().hex}"
    admin_engine = create_engine(DATABASE_URL)
    engine: Engine | None = None
    try:
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine = create_engine(DATABASE_URL, connect_args={"options": f"-csearch_path={schema}"})
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            connection.execute(text("INSERT INTO alembic_version VALUES ('restore-test-head')"))
        statements: list[str] = []
        event.listen(
            engine,
            "before_cursor_execute",
            lambda _connection, _cursor, statement, *_args: statements.append(statement),
        )
        yield engine, statements
    finally:
        if engine is not None:
            engine.dispose()
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin_engine.dispose()


def test_verifier_sets_read_only_before_inspecting_postgresql(
    postgres_restore_engine: tuple[Engine, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, statements = postgres_restore_engine
    monkeypatch.setenv("FIN_GPT_SECRET_KEY", "test-secret-key-with-more-than-thirty-two-bytes")
    monkeypatch.setenv("FINGPT_EVIDENCE_STORAGE_MODE", "filesystem")

    result = verify_restored_database(engine, "restore-test-head")
    app_ready = verify_application_readiness(engine)

    canary_id = uuid4()
    with Session(engine) as session:
        session.add(User(id=canary_id, username="canary-user", password_hash="hash"))
        session.commit()
    statements.clear()
    canary_found = verify_synthetic_canary_record(engine, str(canary_id))
    canary_missing = verify_synthetic_canary_record(engine, str(uuid4()))

    assert result["migration_head_matches"] is True
    assert result["schema_valid"] is True
    assert app_ready is True
    assert canary_found is True
    assert canary_missing is False
    assert statements[0] == "SET TRANSACTION READ ONLY"
    assert statements.count("SET TRANSACTION READ ONLY") == 2
    assert any(statement.startswith("SELECT") for statement in statements[1:])
