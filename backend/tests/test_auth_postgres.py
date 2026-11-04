"""PostgreSQL integration tests for shared, atomic authentication controls."""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from fingpt import create_app
from fingpt.persistence import Base

DATABASE_URL = os.environ.get("FINGPT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="FINGPT_TEST_DATABASE_URL is not configured")


@pytest.fixture
def postgres_auth_client() -> Iterator[tuple[FlaskClient, Flask, Engine]]:
    if DATABASE_URL is None:
        pytest.skip("FINGPT_TEST_DATABASE_URL is not configured")

    schema = f"auth_test_{uuid4().hex}"
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
    yield app.test_client(), app, engine
    engine.dispose()
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
    admin_engine.dispose()


def test_login_throttle_is_atomic_and_shared_by_postgresql(
    postgres_auth_client: tuple[FlaskClient, Flask, Engine],
) -> None:
    client, app, _ = postgres_auth_client
    app.test_cli_runner().invoke(
        args=["bootstrap-owner", "owner"],
        input="Strong test password! 2026\nStrong test password! 2026\n",
    )

    responses = [
        client.post(
            "/api/auth/login",
            json={"username": "owner", "password": "incorrect password"},
            environ_base={"REMOTE_ADDR": "192.0.2.80"},
        )
        for _ in range(11)
    ]

    assert responses[0].status_code == 401
    assert responses[-1].status_code == 429
    assert responses[-1].headers.get("Retry-After") is not None


def test_login_throttle_does_not_block_a_different_client(
    postgres_auth_client: tuple[FlaskClient, Flask, Engine],
) -> None:
    client, app, _ = postgres_auth_client
    app.test_cli_runner().invoke(
        args=["bootstrap-owner", "owner"],
        input="Strong test password! 2026\nStrong test password! 2026\n",
    )
    for _ in range(10):
        client.post(
            "/api/auth/login",
            json={"username": "owner", "password": "incorrect password"},
            environ_base={"REMOTE_ADDR": "192.0.2.81"},
        )

    response = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "Strong test password! 2026"},
        environ_base={"REMOTE_ADDR": "192.0.2.82"},
    )

    assert response.status_code == 200
