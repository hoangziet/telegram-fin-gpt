"""Authentication tests covering abuse cases and session ownership."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from argon2 import PasswordHasher
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from fingpt import create_app
from fingpt.persistence import Base
from fingpt.persistence.models import AuthSession, User


@pytest.fixture
def app() -> Iterator[Flask]:
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
    yield app
    engine.dispose()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def test_bootstrap_owner_is_one_time_and_never_exposes_password_hash(app: Flask) -> None:
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["bootstrap-owner", "Owner"],
        input="Strong test password! 2026\nStrong test password! 2026\n",
    )

    assert result.exit_code == 0
    assert "created" in result.output.lower()
    assert "password_hash" not in result.output

    second_result = runner.invoke(
        args=["bootstrap-owner", "other"],
        input="Another strong password! 2026\nAnother strong password! 2026\n",
    )
    assert second_result.exit_code != 0


def test_bootstrap_rejects_short_password(client: FlaskClient, app: Flask) -> None:
    result = app.test_cli_runner().invoke(
        args=["bootstrap-owner", "owner"],
        input="short\nshort\n",
    )

    assert result.exit_code != 0
    assert client.get("/api/auth/me").status_code == 401


def test_login_issues_http_only_cookie_and_server_side_session(client: FlaskClient, app: Flask) -> None:
    app.test_cli_runner().invoke(
        args=["bootstrap-owner", "Owner"],
        input="Strong test password! 2026\nStrong test password! 2026\n",
    )

    response = client.post(
        "/api/auth/login",
        json={"username": "OWNER", "password": "Strong test password! 2026"},
        environ_base={"REMOTE_ADDR": "192.0.2.10"},
    )

    assert response.status_code == 200
    assert response.json is not None
    assert response.json["user"]["username"] == "owner"
    assert response.json["csrfToken"]
    cookie = response.headers["Set-Cookie"]
    assert cookie.startswith("fingpt_session=")
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert "Secure" not in cookie

    with Session(app.extensions["database_engine"]) as database:
        auth_session = database.scalar(select(AuthSession))
        assert auth_session is not None
        assert auth_session.token_hash not in cookie
        assert len(auth_session.token_hash) == 64


def test_protected_identity_uses_authenticated_session_not_client_user_id(client: FlaskClient, app: Flask) -> None:
    cli = app.test_cli_runner()
    cli.invoke(args=["bootstrap-owner", "Owner"], input="Strong test password! 2026\nStrong test password! 2026\n")
    with Session(app.extensions["database_engine"]) as database:
        hasher: PasswordHasher = app.extensions["password_hasher"]
        other_user = User(username="other", password_hash=hasher.hash("Another secure password! 2026"))
        database.add(other_user)
        database.commit()
        other_user_id = str(other_user.id)
    login_response = client.post(
        "/api/auth/login", json={"username": "owner", "password": "Strong test password! 2026"}
    )

    response = client.get(
        "/api/auth/me",
        headers={"X-User-ID": other_user_id},
    )

    assert login_response.status_code == 200
    assert response.status_code == 200
    assert response.json is not None
    assert login_response.json is not None
    assert response.json["user"]["id"] == login_response.json["user"]["id"]


def test_login_failure_is_generic_and_rate_limited(client: FlaskClient, app: Flask) -> None:
    app.test_cli_runner().invoke(
        args=["bootstrap-owner", "owner"],
        input="Strong test password! 2026\nStrong test password! 2026\n",
    )

    responses = [
        client.post(
            "/api/auth/login",
            json={"username": "owner", "password": "incorrect password"},
            environ_base={"REMOTE_ADDR": "192.0.2.20"},
        )
        for _ in range(11)
    ]

    assert responses[0].status_code == 401
    assert responses[0].json == {"error": {"code": "invalid_credentials", "message": "Invalid username or password."}}
    assert responses[-1].status_code == 429
    assert int(responses[-1].headers["Retry-After"]) > 0


def test_logout_requires_csrf_and_revokes_the_session(client: FlaskClient, app: Flask) -> None:
    app.test_cli_runner().invoke(
        args=["bootstrap-owner", "owner"],
        input="Strong test password! 2026\nStrong test password! 2026\n",
    )
    login_response = client.post(
        "/api/auth/login", json={"username": "owner", "password": "Strong test password! 2026"}
    )
    assert login_response.json is not None

    rejected = client.post("/api/auth/logout")
    accepted = client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": login_response.json["csrfToken"]},
    )

    assert rejected.status_code == 403
    assert accepted.status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    with Session(app.extensions["database_engine"]) as database:
        user = database.scalar(select(User))
        assert user is not None


def test_login_rotates_and_revokes_a_preexisting_session(client: FlaskClient, app: Flask) -> None:
    app.test_cli_runner().invoke(
        args=["bootstrap-owner", "owner"],
        input="Strong test password! 2026\nStrong test password! 2026\n",
    )
    first_login = client.post("/api/auth/login", json={"username": "owner", "password": "Strong test password! 2026"})
    assert first_login.status_code == 200
    previous_cookie = client.get_cookie("fingpt_session")
    assert previous_cookie is not None

    previous_session_client = app.test_client()
    previous_session_client.set_cookie("fingpt_session", previous_cookie.value)
    second_login = client.post("/api/auth/login", json={"username": "owner", "password": "Strong test password! 2026"})

    assert second_login.status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    assert previous_session_client.get("/api/auth/me").status_code == 401


def test_no_public_registration_endpoint(client: FlaskClient) -> None:
    response = client.post("/api/auth/register", json={"username": "other", "password": "Not allowed"})

    assert response.status_code == 401


def test_malformed_non_ascii_session_cookie_is_rejected_without_server_error(client: FlaskClient) -> None:
    client.set_cookie("fingpt_session", "not-a-valid-session-💥")

    response = client.get("/api/auth/me")

    assert response.status_code == 401


@pytest.fixture
def password_headers(client: FlaskClient, app: Flask) -> dict[str, str]:
    app.test_cli_runner().invoke(
        args=["bootstrap-owner", "owner"],
        input="Strong test password! 2026\nStrong test password! 2026\n",
    )
    response = client.post("/api/auth/login", json={"username": "owner", "password": "Strong test password! 2026"})
    assert response.status_code == 200
    assert response.json is not None
    return {"X-CSRF-Token": response.json["csrfToken"]}


def test_change_password_requires_authentication(client: FlaskClient) -> None:
    assert client.post("/api/auth/password").status_code == 401


def test_change_password_requires_csrf(client: FlaskClient, password_headers: dict[str, str]) -> None:
    assert client.post("/api/auth/password").status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"currentPassword": "password"},
        {"newPassword": "password"},
        {"currentPassword": 123, "newPassword": "password"},
        {"currentPassword": "password", "newPassword": 123},
    ],
)
def test_change_password_rejects_invalid_body(
    client: FlaskClient, password_headers: dict[str, str], body: object
) -> None:
    response = client.post("/api/auth/password", json=body, headers=password_headers)
    assert response.status_code == 400
    assert response.json is not None
    assert response.json["error"]["code"] == "invalid_request"


def test_change_password_rejects_wrong_current_password(client: FlaskClient, password_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/auth/password",
        headers=password_headers,
        json={"currentPassword": "wrong password", "newPassword": "Another strong password! 2026"},
    )
    assert response.status_code == 401
    assert response.json is not None
    assert response.json["error"]["code"] == "invalid_current_password"


@pytest.mark.parametrize("new_password", ["short", "x" * 129, "Strong test password! 2026"])
def test_change_password_rejects_invalid_new_password(
    client: FlaskClient, password_headers: dict[str, str], new_password: str
) -> None:
    response = client.post(
        "/api/auth/password",
        headers=password_headers,
        json={"currentPassword": "Strong test password! 2026", "newPassword": new_password},
    )
    assert response.status_code == 422
    assert response.json is not None
    assert response.json["error"]["code"] == "invalid_new_password"


def test_change_password_updates_login_credentials(client: FlaskClient, password_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/auth/password",
        headers=password_headers,
        json={"currentPassword": "Strong test password! 2026", "newPassword": "Another strong password! 2026"},
    )
    assert response.status_code == 200
    assert response.json == {"status": "updated"}
    assert (
        client.post(
            "/api/auth/login", json={"username": "owner", "password": "Strong test password! 2026"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login", json={"username": "owner", "password": "Another strong password! 2026"}
        ).status_code
        == 200
    )


def test_change_password_rolls_back_database_failure(
    client: FlaskClient, password_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as patch:

        def fail_commit(database: Session) -> None:
            raise SQLAlchemyError("private database details")

        patch.setattr(Session, "commit", fail_commit)
        response = client.post(
            "/api/auth/password",
            headers=password_headers,
            json={"currentPassword": "Strong test password! 2026", "newPassword": "Another strong password! 2026"},
        )
    assert response.status_code == 503
    assert response.json == {"error": {"code": "service_unavailable", "message": "Service is unavailable."}}
    assert (
        client.post(
            "/api/auth/login", json={"username": "owner", "password": "Strong test password! 2026"}
        ).status_code
        == 200
    )
