"""Shared test fixtures for the backend suite."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from argon2 import PasswordHasher
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from fingpt import create_app
from fingpt.persistence import Base
from fingpt.persistence.models import User


@pytest.fixture
def draft_app() -> Iterator[tuple[Flask, FlaskClient, UUID]]:
    """Create an isolated owner session and SQLite database for API workflows."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    app = create_app(
        {
            "DATABASE_URL": "sqlite://",
            "TESTING": True,
            "SECRET_KEY": "test-secret-key-at-least-thirty-two-bytes",
            "SESSION_COOKIE_SECURE": False,
            "PASSWORD_HASHER": PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1),
        },
        engine=engine,
    )
    password = "Strong draft test password! 2026"
    app.test_cli_runner().invoke(args=["bootstrap-owner", "owner"], input=f"{password}\n{password}\n")
    client = app.test_client()
    login_response = client.post("/api/auth/login", json={"username": "owner", "password": password})
    assert login_response.status_code == 200
    assert login_response.json is not None
    client.environ_base["HTTP_X_CSRF_TOKEN"] = login_response.json["csrfToken"]
    with Session(engine, expire_on_commit=False) as database:
        owner = database.scalar(select(User).where(User.username == "owner"))
        assert owner is not None
        yield app, client, owner.id
    engine.dispose()
