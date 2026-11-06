"""Application factory for the FinGPT API."""

from __future__ import annotations

import hmac
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from flask import Flask, abort, g, jsonify, request, send_from_directory
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from werkzeug.exceptions import RequestEntityTooLarge

from fingpt.adapters.anthropic_chat import AnthropicChat
from fingpt.adapters.anthropic_image_extractor import AnthropicImageExtractor
from fingpt.adapters.anthropic_insight import AnthropicInsight
from fingpt.adapters.anthropic_router import AnthropicRouter
from fingpt.adapters.evidence_storage import create_evidence_storage
from fingpt.api.auth import register_auth
from fingpt.api.chat import register_chat
from fingpt.api.drafts import register_drafts
from fingpt.api.imports import register_imports
from fingpt.api.transactions import register_transactions
from fingpt.api.webhooks import register_webhooks
from fingpt.operations import register_operations
from fingpt.persistence.models import AuthSession, User
from fingpt.services.auth import digest_session_token
from fingpt.services.chat_orchestration import ChatOrchestrator, InsightSpecialist
from fingpt.services.insights import spending_trend


def create_app(overrides: dict[str, Any] | None = None, *, engine: Engine | None = None) -> Flask:
    """Create a configured Flask application without starting background work."""
    app = Flask(__name__, static_folder=None)
    cookie_secure = _environment_bool("AUTH_COOKIE_SECURE", default=True)
    app.config.from_mapping(
        DATABASE_URL=os.environ.get("DATABASE_URL"),
        SECRET_KEY=os.environ.get("FIN_GPT_SECRET_KEY"),
        SESSION_COOKIE_SECURE=cookie_secure,
        AUTH_COOKIE_NAME="__Host-fingpt_session" if cookie_secure else "fingpt_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        FINGPT_DEPLOYMENT_ENV=os.environ.get("FINGPT_DEPLOYMENT_ENV", "development"),
        EVIDENCE_STORAGE_MODE=os.environ.get("FINGPT_EVIDENCE_STORAGE_MODE", "filesystem"),
        EVIDENCE_STORAGE_DIRECTORY=os.environ.get("FINGPT_EVIDENCE_DIRECTORY", ".local/evidence"),
        EVIDENCE_R2_ACCOUNT_ID=os.environ.get("FINGPT_EVIDENCE_R2_ACCOUNT_ID"),
        EVIDENCE_R2_BUCKET=os.environ.get("FINGPT_EVIDENCE_R2_BUCKET"),
        EVIDENCE_R2_ACCESS_KEY_ID=os.environ.get("FINGPT_EVIDENCE_R2_ACCESS_KEY_ID"),
        EVIDENCE_R2_SECRET_ACCESS_KEY=os.environ.get("FINGPT_EVIDENCE_R2_SECRET_ACCESS_KEY"),
        ANTHROPIC_API_KEY=os.environ.get("FINGPT_ANTHROPIC_API_KEY"),
        ANTHROPIC_MODEL=os.environ.get("FINGPT_ANTHROPIC_MODEL"),
        WEB_DIRECTORY=os.environ.get(
            "FINGPT_WEB_DIRECTORY",
            str(Path(__file__).resolve().parents[2] / "web" / "dist"),
        ),
    )
    if overrides:
        app.config.update(overrides)
    if not overrides or "AUTH_COOKIE_NAME" not in overrides:
        app.config["AUTH_COOKIE_NAME"] = (
            "__Host-fingpt_session" if app.config["SESSION_COOKIE_SECURE"] else "fingpt_session"
        )

    if app.config["TESTING"] and not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = "test-only-secret-key-not-for-deployment-32bytes"
    secret_key = app.config["SECRET_KEY"]
    if not isinstance(secret_key, str) or len(secret_key.encode()) < 32 or "replace-with" in secret_key:
        raise RuntimeError("Set FIN_GPT_SECRET_KEY to a random secret of at least 32 bytes.")
    if app.config["FINGPT_DEPLOYMENT_ENV"] == "production" and app.config["EVIDENCE_STORAGE_MODE"] != "r2":
        raise RuntimeError("Production must use private R2 storage for evidence.")

    hasher: PasswordHasher = app.config.get("PASSWORD_HASHER") or PasswordHasher(
        time_cost=3,
        memory_cost=65_536,
        parallelism=4,
        hash_len=32,
        salt_len=16,
    )
    app.extensions["password_hasher"] = hasher
    app.extensions["dummy_password_hash"] = hasher.hash(os.urandom(32).hex())
    app.extensions["database_engine"] = engine or _create_engine(app.config["DATABASE_URL"])
    configured_storage = app.config.get("EVIDENCE_STORAGE")
    if configured_storage is not None:
        app.extensions["evidence_storage"] = configured_storage
    else:
        app.extensions["evidence_storage"] = create_evidence_storage(
            mode=app.config["EVIDENCE_STORAGE_MODE"],
            local_root=app.config["EVIDENCE_STORAGE_DIRECTORY"],
            r2_account_id=app.config["EVIDENCE_R2_ACCOUNT_ID"],
            r2_bucket=app.config["EVIDENCE_R2_BUCKET"],
            r2_access_key_id=app.config["EVIDENCE_R2_ACCESS_KEY_ID"],
            r2_secret_access_key=app.config["EVIDENCE_R2_SECRET_ACCESS_KEY"],
        )
    api_key = app.config["ANTHROPIC_API_KEY"]
    model = app.config["ANTHROPIC_MODEL"]
    if api_key and model:
        app.extensions["image_extractor"] = AnthropicImageExtractor(api_key, model)
        app.extensions["chat_assistant"] = AnthropicChat(api_key, model)
    if app.config.get("CHAT_ASSISTANT") is not None:
        app.extensions["chat_assistant"] = app.config["CHAT_ASSISTANT"]

    router = app.config.get("CHAT_ROUTER")
    if router is None and api_key and model:
        router = AnthropicRouter(api_key, model)
    app.extensions["chat_orchestrator"] = ChatOrchestrator(router)

    narrator = app.config.get("INSIGHT_ASSISTANT")
    if narrator is None and api_key and model:
        narrator = AnthropicInsight(api_key, model)
    app.extensions["insight_specialist"] = InsightSpecialist(narrator, spending_trend)

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(_error: RequestEntityTooLarge) -> tuple[Any, int]:
        """Keep API upload size failures in the standard JSON error shape."""
        return jsonify(error={"code": "request_too_large", "message": "Request body exceeds the allowed size."}), 413

    register_auth(app)
    register_chat(app)
    register_drafts(app)
    register_imports(app)
    register_transactions(app)
    register_webhooks(app)
    register_operations(app)
    _register_web_routes(app, Path(app.config["WEB_DIRECTORY"]))

    @app.before_request
    def authenticate_api_request() -> tuple[Any, int] | None:
        """Authenticate every API request except the public sign-in route."""
        if request.path.startswith("/api/chat/"):
            request.max_content_length = 65_536
        elif request.path.startswith(("/api/auth/", "/api/drafts")):
            request.max_content_length = 16_384
        if (
            not request.path.startswith("/api/")
            or request.path == "/api/auth/login"
            or request.path in {"/api/health/live", "/api/health/ready"}
            or request.path.startswith("/api/webhooks/telegram/")
        ):
            return None

        database_engine: Engine | None = app.extensions["database_engine"]
        if database_engine is None:
            return jsonify(error={"code": "service_unavailable", "message": "Service is unavailable."}), 503

        database = Session(database_engine, expire_on_commit=False)
        g.database_session = database
        raw_token = request.cookies.get(app.config["AUTH_COOKIE_NAME"])
        if not raw_token:
            return _unauthorized()

        try:
            identity = database.execute(
                select(AuthSession, User)
                .join(User, User.id == AuthSession.user_id)
                .where(
                    AuthSession.token_hash == digest_session_token(raw_token),
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > datetime.now(UTC),
                    User.is_active.is_(True),
                )
            ).one_or_none()
        except SQLAlchemyError:
            app.logger.warning("Authentication store unavailable")
            return jsonify(error={"code": "service_unavailable", "message": "Service is unavailable."}), 503

        if identity is None:
            return _unauthorized()

        auth_session, user = identity
        g.auth_session = auth_session
        g.auth_user = user
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            provided_token = request.headers.get("X-CSRF-Token", "")
            if not provided_token or not _constant_time_match(auth_session.csrf_token, provided_token):
                return jsonify(error={"code": "csrf_failed", "message": "Request verification failed."}), 403
        return None

    @app.teardown_request
    def close_request_database(error: BaseException | None) -> None:
        """Release the per-request database session and roll back failed work."""
        database: Session | None = getattr(g, "database_session", None)
        if database is not None:
            if error is not None:
                database.rollback()
            database.close()

    @app.after_request
    def add_security_headers(response: Any) -> Any:
        """Set browser security defaults and prevent API response caching."""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'",
        )
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health/live")
    @app.get("/api/health/live")
    def liveness() -> tuple[Any, int]:
        """Report API liveness through direct and Static Web Apps proxy routes."""
        return jsonify(status="alive"), 200

    @app.get("/health/ready")
    @app.get("/api/health/ready")
    def readiness() -> tuple[Any, int]:
        """Report database readiness through direct and proxy routes."""
        engine: Engine | None = app.extensions["database_engine"]
        if engine is None:
            return jsonify(status="not_ready", reason="database_not_configured"), 503

        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            app.logger.warning("Database readiness check failed")
            return jsonify(status="not_ready", reason="database_unavailable"), 503

        return jsonify(status="ready"), 200

    return app


def _create_engine(database_url: str | None) -> Engine | None:
    """Create the database engine when a connection URL is configured."""
    if not database_url:
        return None
    return create_engine(database_url, pool_pre_ping=True, pool_size=5, max_overflow=10)


def _register_web_routes(app: Flask, web_directory: Path) -> None:
    """Serve the built SPA from the API origin when a frontend artifact exists."""
    index_file = web_directory / "index.html"
    if not index_file.is_file():
        return

    @app.get("/")
    def web_index() -> Any:
        """Serve the SPA entry point without caching stale application shells."""
        return _send_web_file(web_directory, "index.html", no_cache=True)

    @app.get("/<path:frontend_path>")
    def web_asset_or_route(frontend_path: str) -> Any:
        """Serve a built asset or fall back to the SPA for client-side routes."""
        if frontend_path == "api" or frontend_path.startswith("api/"):
            abort(404)
        if frontend_path == "health" or frontend_path.startswith("health/"):
            abort(404)
        if (web_directory / frontend_path).is_file():
            return _send_web_file(web_directory, frontend_path)
        return _send_web_file(web_directory, "index.html", no_cache=True)


def _send_web_file(web_directory: Path, relative_path: str, *, no_cache: bool = False) -> Any:
    """Send one file from the verified frontend build directory."""
    response = send_from_directory(web_directory, relative_path)
    if no_cache:
        response.headers["Cache-Control"] = "no-cache"
    return response


def _environment_bool(name: str, *, default: bool) -> bool:
    """Parse a strict boolean environment setting."""
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise RuntimeError(f"{name} must be set to true or false.")


def _constant_time_match(expected: str, provided: str) -> bool:
    """Compare CSRF values without leaking prefix matches through timing."""
    return hmac.compare_digest(expected, provided)


def _unauthorized() -> tuple[Any, int]:
    """Return the generic response for absent, expired, or revoked sessions."""
    return jsonify(error={"code": "authentication_required", "message": "Sign-in is required."}), 401
