"""Track manual outbox retry generations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c41d5a8e2f0"
down_revision: str | None = "b4d28f6a1c09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add durable operator retry accounting."""
    op.add_column(
        "outbox_messages",
        sa.Column("operator_retry_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_outbox_messages_operator_retry_nonnegative",
        "outbox_messages",
        "operator_retry_count >= 0",
    )


def downgrade() -> None:
    """Remove operator retry accounting."""
    op.drop_constraint("ck_outbox_messages_operator_retry_nonnegative", "outbox_messages", type_="check")
    op.drop_column("outbox_messages", "operator_retry_count")
