"""Password, session, and login-throttling primitives."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from fingpt.persistence.models import AuthSession, LoginThrottle, User

SESSION_LIFETIME = timedelta(hours=12)
LOGIN_WINDOW = timedelta(minutes=15)
MAX_LOGIN_ATTEMPTS = 10
_USERNAME_PATTERN = re.compile(r"[a-z0-9._-]{3,128}\Z", re.ASCII)


def normalize_username(value: str) -> str | None:
    """Return canonical ASCII username or `None` when it is not allowed."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    if _USERNAME_PATTERN.fullmatch(normalized) is None:
        return None
    return normalized


def validate_password(value: str) -> str | None:
    """Return a validation reason for a new password, otherwise `None`."""
    if not 15 <= len(value) <= 128:
        return "Password must contain between 15 and 128 characters."
    if len(value.encode("utf-8")) > 1024:
        return "Password is too long."
    return None


def verify_password(hasher: PasswordHasher, stored_hash: str, password: str) -> bool:
    """Verify a password while treating invalid stored hashes as a failed login."""
    try:
        return hasher.verify(stored_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def digest_session_token(token: str) -> str:
    """Hash a high-entropy bearer token before storing or querying it."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def login_bucket(secret_key: str, remote_address: str) -> str:
    """Create a non-reversible per-client login throttle key."""
    return hmac.new(secret_key.encode(), remote_address.encode(), hashlib.sha256).hexdigest()


def consume_login_attempt(database: Session, bucket_hash: str, now: datetime) -> tuple[int, datetime]:
    """Atomically consume one shared fixed-window login attempt."""
    cutoff = now - LOGIN_WINDOW
    engine_name = database.get_bind().dialect.name
    expired = LoginThrottle.window_started_at <= cutoff
    values = {"bucket_hash": bucket_hash, "attempt_count": 1, "window_started_at": now}
    if engine_name == "postgresql":
        statement: Any = postgresql_insert(LoginThrottle).values(**values)
    else:
        statement = sqlite_insert(LoginThrottle).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[LoginThrottle.bucket_hash],
        set_={
            "attempt_count": case((expired, 1), else_=LoginThrottle.attempt_count + 1),
            "window_started_at": case((expired, now), else_=LoginThrottle.window_started_at),
        },
    ).returning(LoginThrottle.attempt_count)
    attempts: int = int(database.execute(statement).scalar_one())
    window_started_at = database.scalar(
        select(LoginThrottle.window_started_at).where(LoginThrottle.bucket_hash == bucket_hash)
    )
    if window_started_at is None:
        raise RuntimeError("Login throttle row was not saved.")
    if window_started_at.tzinfo is None:
        window_started_at = window_started_at.replace(tzinfo=UTC)
    return int(attempts), window_started_at


def create_session(database: Session, user_id: UUID, now: datetime) -> tuple[AuthSession, str]:
    """Create a short-lived session and return its raw cookie token once."""
    raw_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    auth_session = AuthSession(
        user_id=user_id,
        token_hash=digest_session_token(raw_token),
        csrf_token=csrf_token,
        expires_at=now + SESSION_LIFETIME,
    )
    database.add(auth_session)
    return auth_session, raw_token


def public_user(user: User) -> dict[str, Any]:
    """Select only safe account fields for browser responses."""
    return {"id": str(user.id), "username": user.username}
