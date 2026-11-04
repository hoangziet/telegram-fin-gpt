"""Add durable proposals for confirmed transaction changes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7d8b409a1c52"
down_revision: str | None = "52a1d0c6b9ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create transaction change proposals and optimistic versions."""
    op.add_column(
        "transactions",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_check_constraint("version_positive", "transactions", "version > 0")
    op.create_table(
        "transaction_changes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("expected_transaction_version", sa.Integer(), nullable=False),
        sa.Column("proposed_state", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="proposed", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("confirmation_key", sa.String(length=128), nullable=True),
        sa.Column("confirmation_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("action IN ('updated', 'deleted', 'restored')", name="action_value"),
        sa.CheckConstraint("status IN ('proposed', 'confirmed', 'cancelled', 'stale')", name="status_value"),
        sa.CheckConstraint("expected_transaction_version > 0", name="expected_version_positive"),
        sa.CheckConstraint("version > 0", name="version_positive"),
        sa.CheckConstraint("length(idempotency_key) BETWEEN 1 AND 128", name="idempotency_key_length"),
        sa.CheckConstraint("length(request_hash) = 64", name="request_hash_length"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["transaction_id", "user_id"],
            ["transactions.id", "transactions.user_id"],
            name="fk_transaction_change_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_transaction_change_idempotency"),
        sa.UniqueConstraint("user_id", "confirmation_key", name="uq_transaction_change_confirmation"),
    )
    op.create_index("ix_transaction_changes_user_id", "transaction_changes", ["user_id"])
    op.create_index("ix_transaction_changes_transaction_id", "transaction_changes", ["transaction_id"])


def downgrade() -> None:
    """Remove change proposals and transaction versions."""
    op.drop_index("ix_transaction_changes_transaction_id", table_name="transaction_changes")
    op.drop_index("ix_transaction_changes_user_id", table_name="transaction_changes")
    op.drop_table("transaction_changes")
    op.drop_constraint("version_positive", "transactions", type_="check")
    op.drop_column("transactions", "version")
