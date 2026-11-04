"""PostgreSQL-backed records for identity, drafts, transactions, and evidence."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class User(Base):
    """A product account; initial deployment bootstraps one owner account."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("length(username) BETWEEN 3 AND 128", name="username_length"),
        CheckConstraint("username = lower(username)", name="username_normalized"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BotConnection(Base):
    """A user's Telegram bot configuration with secrets kept outside the DB."""

    __tablename__ = "bot_connections"
    __table_args__ = (
        UniqueConstraint("telegram_bot_id", name="uq_bot_telegram_id"),
        CheckConstraint("length(secret_name) > 0", name="secret_name_present"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    telegram_bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    secret_name: Mapped[str] = mapped_column(String(256), nullable=False)
    webhook_secret_name: Mapped[str | None] = mapped_column(String(256))
    allowed_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Conversation(Base):
    """A channel-specific context boundary owned by one user."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("user_id", "channel", "external_conversation_id", name="uq_conversation_channel_ref"),
        UniqueConstraint("id", "user_id", name="uq_conversation_id_user"),
        CheckConstraint("channel IN ('web', 'telegram')", name="channel_value"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    external_conversation_id: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Evidence(Base):
    """Private source evidence such as text, receipt images, or CSV files."""

    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_evidence_id_user"),
        CheckConstraint("source_type IN ('text', 'image', 'csv')", name="source_type_value"),
        CheckConstraint(
            "(source_type = 'text' AND source_text IS NOT NULL) OR "
            "(source_type IN ('image', 'csv') AND storage_key IS NOT NULL)",
            name="source_content_present",
        ),
        CheckConstraint("byte_size >= 0", name="byte_size_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_text: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(String(512))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    media_type: Mapped[str | None] = mapped_column(String(128))
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Draft(Base):
    """Durable pending interpretation of one inbound user request."""

    __tablename__ = "drafts"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_draft_id_user"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_draft_user_idempotency"),
        ForeignKeyConstraint(
            ["conversation_id", "user_id"],
            ["conversations.id", "conversations.user_id"],
            name="fk_draft_conversation_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "user_id"],
            ["evidence.id", "evidence.user_id"],
            name="fk_draft_evidence_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('open', 'completed', 'cancelled', 'expired')", name="status_value"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("length(idempotency_key) BETWEEN 1 AND 128", name="idempotency_key_length"),
        CheckConstraint("length(request_hash) = 64", name="request_hash_length"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    evidence_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DraftItem(Base):
    """An independently reviewable proposed transaction from a draft."""

    __tablename__ = "draft_items"
    __table_args__ = (
        UniqueConstraint("draft_id", "item_index", name="uq_draft_item_order"),
        UniqueConstraint("id", "user_id", name="uq_draft_item_id_user"),
        ForeignKeyConstraint(
            ["draft_id", "user_id"],
            ["drafts.id", "drafts.user_id"],
            name="fk_draft_item_draft_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint("status IN ('incomplete', 'ready', 'confirmed', 'cancelled')", name="status_value"),
        CheckConstraint("item_index >= 0", name="item_index_nonnegative"),
        CheckConstraint("version > 0", name="version_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    draft_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    item_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="incomplete")
    extracted_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    missing_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Transaction(Base):
    """A confirmed income or expense with exact money and explicit local time."""

    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_transaction_id_user"),
        ForeignKeyConstraint(
            ["draft_item_id", "user_id"],
            ["draft_items.id", "draft_items.user_id"],
            name="fk_transaction_draft_item_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint("kind IN ('income', 'expense')", name="kind_value"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "currency IN ('BHD', 'EUR', 'JPY', 'KWD', 'USD', 'VND')",
            name="currency_supported",
        ),
        CheckConstraint(
            "day_period IN ('morning', 'noon', 'afternoon', 'evening')",
            name="day_period_value",
        ),
        UniqueConstraint("draft_item_id", name="uq_transaction_draft_item"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_transaction_idempotency"),
        CheckConstraint("length(idempotency_key) BETWEEN 1 AND 128", name="idempotency_key_length"),
        CheckConstraint("length(request_hash) = 64", name="request_hash_length"),
        CheckConstraint("version > 0", name="version_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    draft_item_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="VND")
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    local_time: Mapped[time | None] = mapped_column(Time)
    day_period: Mapped[str] = mapped_column(String(16), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Ho_Chi_Minh")
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TransactionChange(Base):
    """Durable proposed edit, delete, or restoration awaiting confirmation."""

    __tablename__ = "transaction_changes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["transaction_id", "user_id"],
            ["transactions.id", "transactions.user_id"],
            name="fk_transaction_change_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_transaction_change_idempotency"),
        UniqueConstraint("user_id", "confirmation_key", name="uq_transaction_change_confirmation"),
        CheckConstraint("action IN ('updated', 'deleted', 'restored')", name="action_value"),
        CheckConstraint("status IN ('proposed', 'confirmed', 'cancelled', 'stale')", name="status_value"),
        CheckConstraint("expected_transaction_version > 0", name="expected_version_positive"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("length(idempotency_key) BETWEEN 1 AND 128", name="idempotency_key_length"),
        CheckConstraint("length(request_hash) = 64", name="request_hash_length"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    transaction_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    expected_transaction_version: Mapped[int] = mapped_column(Integer, nullable=False)
    proposed_state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmation_key: Mapped[str | None] = mapped_column(String(128))
    confirmation_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TransactionRevision(Base):
    """Immutable before/after snapshot used for audit and safe undo."""

    __tablename__ = "transaction_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["transaction_id", "user_id"],
            ["transactions.id", "transactions.user_id"],
            name="fk_revision_transaction_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("transaction_id", "revision_number", name="uq_transaction_revision_order"),
        CheckConstraint("action IN ('created', 'updated', 'deleted', 'restored')", name="action_value"),
        CheckConstraint("revision_number > 0", name="revision_number_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    transaction_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    actor_user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class WebhookReceipt(Base):
    """Idempotent receipt of a Telegram update before asynchronous processing."""

    __tablename__ = "webhook_receipts"
    __table_args__ = (
        UniqueConstraint("bot_connection_id", "external_update_id", name="uq_webhook_update"),
        CheckConstraint("status IN ('accepted', 'processing', 'processed', 'failed')", name="status_value"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    bot_connection_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("bot_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="accepted")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    telegram_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxMessage(Base):
    """Durable event to publish after its database transaction commits."""

    __tablename__ = "outbox_messages"
    __table_args__ = (
        UniqueConstraint("deduplication_key", name="uq_outbox_deduplication"),
        CheckConstraint("status IN ('pending', 'published', 'failed')", name="status_value"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("operator_retry_count >= 0", name="operator_retry_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    deduplication_key: Mapped[str] = mapped_column(String(128), nullable=False)
    message_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    operator_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    """Revocable server-side web session; only the bearer token hash is stored."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("length(token_hash) = 64", name="token_hash_length"),
        CheckConstraint("length(csrf_token) >= 32", name="csrf_token_length"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginThrottle(Base):
    """Shared fixed-window counter keyed by a one-way client-address digest."""

    __tablename__ = "login_throttles"
    __table_args__ = (CheckConstraint("attempt_count > 0", name="attempt_count_positive"),)

    bucket_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
