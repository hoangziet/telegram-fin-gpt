"""PostgreSQL integration coverage for draft and evidence constraints."""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from argon2 import PasswordHasher
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from fingpt import create_app
from fingpt.persistence import Base
from fingpt.persistence.models import Evidence, Transaction, User

DATABASE_URL = os.environ.get("FINGPT_TEST_DATABASE_URL")
TEST_PASSWORD = "Strong PostgreSQL draft test password! 2026"
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="FINGPT_TEST_DATABASE_URL is not configured")


@pytest.fixture
def postgres_draft_client() -> Iterator[tuple[FlaskClient, Flask, Engine, UUID]]:
    if DATABASE_URL is None:
        pytest.skip("FINGPT_TEST_DATABASE_URL is not configured")

    schema = f"draft_test_{uuid4().hex}"
    admin_engine = create_engine(DATABASE_URL)
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')

    engine = create_engine(DATABASE_URL, connect_args={"options": f"-csearch_path={schema}"})
    Base.metadata.create_all(engine)
    app = create_app(
        {
            "DATABASE_URL": DATABASE_URL,
            "TESTING": True,
            "SECRET_KEY": "test-secret-key-at-least-thirty-two-bytes",
            "SESSION_COOKIE_SECURE": False,
            "PASSWORD_HASHER": PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1),
        },
        engine=engine,
    )
    app.test_cli_runner().invoke(args=["bootstrap-owner", "owner"], input=f"{TEST_PASSWORD}\n{TEST_PASSWORD}\n")
    client = app.test_client()
    login = client.post("/api/auth/login", json={"username": "owner", "password": TEST_PASSWORD})
    assert login.status_code == 200
    assert login.json is not None
    client.environ_base["HTTP_X_CSRF_TOKEN"] = login.json["csrfToken"]

    with Session(engine) as database:
        owner = database.scalar(select(User).where(User.username == "owner"))
        assert owner is not None
        yield client, app, engine, owner.id

    engine.dispose()
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
    admin_engine.dispose()


def test_text_draft_and_source_evidence_commit_together_in_postgresql(
    postgres_draft_client: tuple[FlaskClient, Flask, Engine, UUID],
) -> None:
    client, _, engine, owner_id = postgres_draft_client

    response = client.post(
        "/api/drafts",
        json={"text": "8h sáng ăn bánh mì 15k"},
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 201
    with Session(engine) as database:
        evidence = database.scalar(select(Evidence).where(Evidence.user_id == owner_id))
        assert evidence is not None
        assert evidence.source_text == "8h sáng ăn bánh mì 15k"
        assert database.scalar(select(func.count()).select_from(Transaction)) == 0
