"""Read-only checks for a restored FinGPT PostgreSQL database."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, inspect, literal, make_url, or_, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fingpt.persistence import Base, Draft, Evidence, Transaction, User


def expected_migration_head() -> str:
    """Return the single migration head shipped with this application image."""
    migration_dir = Path(__file__).resolve().parents[1] / "migrations"
    config = Config()
    config.set_main_option("script_location", str(migration_dir))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("Migration history has no head.")
    return head


def database_url_for_host(database_url: str, host: str | None) -> str:
    """Override only the database host while preserving secret URL components."""
    if not host:
        return database_url
    labels = host.split(".")
    valid_dns_name = len(host) <= 253 and all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels
    )
    if not valid_dns_name:
        raise ValueError("RESTORE_DATABASE_HOST must be a hostname only.")
    return make_url(database_url).set(host=host).render_as_string(hide_password=False)


def verify_restored_database(engine: Engine, expected_head: str) -> dict[str, Any]:
    """Check connectivity, migration version, and schema presence without reading rows."""
    with engine.connect() as connection:
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        connection.execute(text("SELECT 1"))
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        required_tables = set(Base.metadata.tables)
        missing_tables = sorted(required_tables - tables)
        missing_columns: dict[str, list[str]] = {}
        missing_constraints: dict[str, list[str]] = {}
        missing_indexes: dict[str, list[str]] = {}
        for table_name, table in Base.metadata.tables.items():
            if table_name not in tables:
                continue
            actual = {column["name"] for column in inspector.get_columns(table_name)}
            missing = sorted({column.name for column in table.columns} - actual)
            if missing:
                missing_columns[table_name] = missing
            expected_constraints = {str(item.name) for item in table.constraints if item.name is not None}
            actual_constraints = {
                str(item["name"])
                for item in inspector.get_check_constraints(table_name)
                + inspector.get_unique_constraints(table_name)
                + inspector.get_foreign_keys(table_name)
                if item.get("name") is not None
            }
            primary_key_name = inspector.get_pk_constraint(table_name).get("name")
            if primary_key_name is not None:
                actual_constraints.add(str(primary_key_name))
            missing_named_constraints = sorted(expected_constraints - actual_constraints)
            if missing_named_constraints:
                missing_constraints[table_name] = missing_named_constraints
            expected_indexes = {str(item.name) for item in table.indexes if item.name is not None}
            actual_indexes = {
                str(item["name"]) for item in inspector.get_indexes(table_name) if item.get("name") is not None
            }
            missing_named_indexes = sorted(expected_indexes - actual_indexes)
            if missing_named_indexes:
                missing_indexes[table_name] = missing_named_indexes
        migration_head: str | None = None
        if "alembic_version" in tables:
            migration_head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        return {
            "connected": True,
            "migration_head_matches": migration_head == expected_head,
            "missing_tables": missing_tables,
            "missing_columns": missing_columns,
            "missing_constraints": missing_constraints,
            "missing_indexes": missing_indexes,
            "schema_valid": not any((missing_tables, missing_columns, missing_constraints, missing_indexes)),
            "migration_head": migration_head,
            "expected_migration_head": expected_head,
        }


def verify_synthetic_canary_record(engine: Engine, canary_id_str: str) -> bool:
    """Verify presence of a designated synthetic canary record in read-only mode without logging contents."""
    try:
        canary_uuid = UUID(canary_id_str)
    except ValueError as error:
        raise ValueError("Canary record ID must be a valid UUID.") from error

    with Session(engine) as session:
        connection = session.connection()
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        stmt = select(literal(True)).where(
            or_(
                select(Draft.id).where(Draft.id == canary_uuid).exists(),
                select(Evidence.id).where(Evidence.id == canary_uuid).exists(),
                select(Transaction.id).where(Transaction.id == canary_uuid).exists(),
                select(User.id).where(User.id == canary_uuid).exists(),
            )
        )
        return bool(session.scalar(stmt))


def verify_application_readiness(engine: Engine) -> bool:
    """Run the real Flask liveness and readiness routes against the restored DB."""
    from fingpt.app import create_app

    def set_read_only(connection: Connection) -> None:
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")

    event.listen(engine, "begin", set_read_only)
    app = None
    try:
        app = create_app(engine=engine)
        client = app.test_client()
        return client.get("/health/live").status_code == 200 and client.get("/health/ready").status_code == 200
    finally:
        event.remove(engine, "begin", set_read_only)
        if app is not None:
            storage = app.extensions["evidence_storage"]
            close = getattr(storage, "close", None)
            if callable(close):
                close()


def main() -> int:
    """Run checks from DATABASE_URL and emit sanitized JSON status."""
    source_database_url = os.environ.get("DATABASE_URL")
    if not source_database_url:
        print(json.dumps({"verified": False, "error": "DATABASE_URL is required"}))
        return 2
    restore_host = os.environ.get("RESTORE_DATABASE_HOST")
    if not restore_host:
        print(json.dumps({"verified": False, "error": "RESTORE_DATABASE_HOST is required"}))
        return 2
    engine: Engine | None = None
    canary_id = os.environ.get("FINGPT_SYNTHETIC_CANARY_ID")
    try:
        database_url = database_url_for_host(source_database_url, restore_host)
        engine = create_engine(database_url, pool_pre_ping=True, pool_size=1, max_overflow=0)
        result = verify_restored_database(engine, expected_migration_head())
        result["application_ready"] = verify_application_readiness(engine)
        if canary_id:
            result["synthetic_record_verified"] = verify_synthetic_canary_record(engine, canary_id)
    except (SQLAlchemyError, RuntimeError, ValueError) as error:
        print(json.dumps({"verified": False, "error_type": type(error).__name__}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    all_valid = result["schema_valid"] and result["migration_head_matches"] and result["application_ready"]
    if canary_id:
        all_valid = all_valid and result.get("synthetic_record_verified", False)
    result["verified"] = all_valid
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    sys.exit(main())
