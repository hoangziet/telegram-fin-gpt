"""Store the Telegram message timestamp for inline callback expiration."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4d28f6a1c09"
down_revision: str | None = "8e3acb5f05c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Retain the creation time of a button's message when accepting its callback."""
    op.add_column("webhook_receipts", sa.Column("telegram_message_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Remove the Telegram button-message timestamp."""
    op.drop_column("webhook_receipts", "telegram_message_at")
