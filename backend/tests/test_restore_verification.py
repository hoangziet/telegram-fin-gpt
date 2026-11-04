"""Tests for sanitized read-only restore verification."""

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from fingpt.persistence import Base, User
from fingpt.restore_verification import (
    database_url_for_host,
    verify_application_readiness,
    verify_restored_database,
    verify_synthetic_canary_record,
)


def test_database_url_host_override_preserves_credentials_and_ssl() -> None:
    source = "postgresql+psycopg://dbuser:s%40cret@source.postgres:5432/fin?sslmode=require"

    restored = database_url_for_host(source, "restore.postgres")

    assert "restore.postgres" in restored
    assert "source.postgres" not in restored
    assert "dbuser:s%40cret" in restored
    assert "sslmode=require" in restored


def test_database_url_host_override_rejects_url_fragments() -> None:
    with pytest.raises(ValueError, match="hostname only"):
        database_url_for_host("postgresql://db/fin", "evil.example/db")


def test_verifies_expected_migration_and_schema() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('head-123')"))

    result = verify_restored_database(engine, "head-123")

    assert result["connected"] is True
    assert result["migration_head_matches"] is True
    assert result["schema_valid"] is True
    assert result["missing_tables"] == []
    assert result["missing_columns"] == {}
    assert result["missing_constraints"] == {}
    assert result["missing_indexes"] == {}
    engine.dispose()


def test_verifies_real_application_health_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIN_GPT_SECRET_KEY", "test-secret-key-with-more-than-thirty-two-bytes")
    monkeypatch.setenv("FINGPT_EVIDENCE_STORAGE_MODE", "filesystem")
    engine = create_engine("sqlite://")

    assert verify_application_readiness(engine) is True

    engine.dispose()


def test_reports_missing_schema_and_wrong_migration_head() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('older-revision')"))

    result = verify_restored_database(engine, "head-123")

    assert result["migration_head_matches"] is False
    assert result["schema_valid"] is False
    assert "transactions" in result["missing_tables"]
    engine.dispose()


def test_verifies_synthetic_canary_record_presence() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    canary_id = uuid.uuid4()
    with Session(engine) as session:
        session.add(User(id=canary_id, username="canary-user", password_hash="hash"))
        session.commit()

    assert verify_synthetic_canary_record(engine, str(canary_id)) is True
    assert verify_synthetic_canary_record(engine, str(uuid.uuid4())) is False

    with pytest.raises(ValueError, match="valid UUID"):
        verify_synthetic_canary_record(engine, "not-a-uuid")

    engine.dispose()
