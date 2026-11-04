"""Username/password authentication endpoints and one-time owner bootstrap."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import click
from argon2 import PasswordHasher
from flask import Blueprint, Flask, Response, current_app, g, jsonify, request
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from fingpt.persistence.models import AuthSession, LoginThrottle, User
from fingpt.services.auth import (
    MAX_LOGIN_ATTEMPTS,
    SESSION_LIFETIME,
    consume_login_attempt,
    create_session,
    digest_session_token,
    login_bucket,
    normalize_username,
    public_user,
    validate_password,
    verify_password,
)

AUTH_BLUEPRINT = Blueprint("auth", __name__, url_prefix="/api/auth")
LOGIN_FAILURE = {"error": {"code": "invalid_credentials", "message": "Invalid username or password."}}


def register_auth(app: Flask) -> None:
    """Register browser authentication routes and the owner bootstrap command."""
    app.register_blueprint(AUTH_BLUEPRINT)

    @app.cli.command("bootstrap-owner")
    @click.argument("username")
    def bootstrap_owner(username: str) -> None:
        """Create the initial owner once, using an interactive password prompt."""
        database = _database_session()
        engine = database.get_bind()
        try:
            with database.begin():
                if engine.dialect.name == "postgresql":
                    database.execute(text("SELECT pg_advisory_xact_lock(741029184)"))
                existing_user = database.scalar(select(User.id).limit(1))
                if existing_user is not None:
                    raise click.ClickException("Owner bootstrap is already complete.")

                normalized = normalize_username(username)
                if normalized is None:
                    raise click.ClickException("Use 3–128 lowercase letters, digits, dots, underscores, or hyphens.")

                password = click.prompt("Owner password", hide_input=True, confirmation_prompt=True)
                reason = validate_password(password)
                if reason is not None:
                    raise click.ClickException(reason)

                hasher: PasswordHasher = current_app.extensions["password_hasher"]
                database.add(User(username=normalized, password_hash=hasher.hash(password)))
        except IntegrityError as error:
            raise click.ClickException("Owner bootstrap could not be completed safely.") from error
        finally:
            database.close()
        click.echo("Owner account created.")


@AUTH_BLUEPRINT.post("/login")
def login() -> tuple[Any, int] | Any:
    """Verify credentials, enforce shared throttling, and issue a server session."""
    if not request.is_json:
        return jsonify(error={"code": "json_required", "message": "Send a JSON request."}), 415

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error={"code": "invalid_request", "message": "Invalid request."}), 400

    secret_key: str = current_app.config["SECRET_KEY"]
    remote_address = request.remote_addr or "unknown"
    bucket_hash = login_bucket(secret_key, remote_address)
    now = datetime.now(UTC)
    if current_app.extensions.get("database_engine") is None:
        return jsonify(error={"code": "auth_unavailable", "message": "Sign-in is temporarily unavailable."}), 503
    database = _database_session()
    hasher: PasswordHasher = current_app.extensions["password_hasher"]
    dummy_hash: str = current_app.extensions["dummy_password_hash"]
    session_token: str | None = None
    csrf_token: str | None = None
    user: User | None = None
    blocked_for: int | None = None

    try:
        with database.begin():
            attempts, window_started_at = consume_login_attempt(database, bucket_hash, now)
            if attempts > MAX_LOGIN_ATTEMPTS:
                blocked_for = max(
                    1,
                    math.ceil((window_started_at + timedelta(minutes=15) - now).total_seconds()),
                )
            else:
                raw_username = body.get("username")
                password = body.get("password")
                normalized = normalize_username(raw_username) if isinstance(raw_username, str) else None
                user = database.scalar(select(User).where(User.username == normalized)) if normalized else None
                stored_hash = user.password_hash if user is not None else dummy_hash
                valid_password = (
                    isinstance(password, str)
                    and len(password) <= 1024
                    and verify_password(hasher, stored_hash, password)
                )

                if user is not None and user.is_active and valid_password:
                    database.execute(delete(LoginThrottle).where(LoginThrottle.bucket_hash == bucket_hash))
                    existing_token = request.cookies.get(current_app.config["AUTH_COOKIE_NAME"])
                    if existing_token:
                        database.execute(
                            update(AuthSession)
                            .where(
                                AuthSession.token_hash == digest_session_token(existing_token),
                                AuthSession.revoked_at.is_(None),
                            )
                            .values(revoked_at=now)
                        )
                    auth_session, session_token = create_session(database, user.id, now)
                    csrf_token = auth_session.csrf_token
                    if hasher.check_needs_rehash(user.password_hash):
                        assert isinstance(password, str)
                        user.password_hash = hasher.hash(password)
                else:
                    user = None
    except SQLAlchemyError:
        current_app.logger.warning("Login attempt failed because the authentication store is unavailable")
        return jsonify(error={"code": "auth_unavailable", "message": "Sign-in is temporarily unavailable."}), 503
    finally:
        database.close()

    if blocked_for is not None:
        response = jsonify(error={"code": "too_many_attempts", "message": "Too many sign-in attempts."})
        response.status_code = 429
        response.headers["Retry-After"] = str(blocked_for)
        return response
    if user is None or session_token is None or csrf_token is None:
        return jsonify(LOGIN_FAILURE), 401

    response = jsonify(user=public_user(user), csrfToken=csrf_token)
    response.set_cookie(
        current_app.config["AUTH_COOKIE_NAME"],
        session_token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        samesite="Strict",
        path="/",
    )
    return response


@AUTH_BLUEPRINT.get("/me")
def current_identity() -> Any:
    """Return the identity authenticated by the server-side session."""
    return jsonify(user=public_user(g.auth_user))


@AUTH_BLUEPRINT.get("/csrf")
def csrf_token() -> Any:
    """Return the CSRF token associated with the authenticated session."""
    return jsonify(csrfToken=g.auth_session.csrf_token)


@AUTH_BLUEPRINT.post("/logout")
def logout() -> Response:
    """Revoke the current server-side session and clear its browser cookie."""
    database: Session = g.database_session
    g.auth_session.revoked_at = datetime.now(UTC)
    database.commit()
    response = current_app.response_class(status=204)
    response.set_cookie(
        current_app.config["AUTH_COOKIE_NAME"],
        "",
        max_age=0,
        httponly=True,
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        samesite="Strict",
        path="/",
    )
    return response


def _database_session() -> Session:
    """Reuse the request database session or create one for public login routes."""
    database: Session | None = getattr(g, "database_session", None)
    if database is None:
        engine = current_app.extensions.get("database_engine")
        if engine is None:
            raise RuntimeError("Authentication database is not configured.")
        database = Session(engine, expire_on_commit=False)
        g.database_session = database
    return database
