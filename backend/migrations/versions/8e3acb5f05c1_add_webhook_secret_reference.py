"""Separate Telegram webhook verification secret from bot API token."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8e3acb5f05c1"
down_revision: str | None = "7d8b409a1c52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a separately managed webhook secret reference to bot connections."""
    op.add_column("bot_connections", sa.Column("webhook_secret_name", sa.String(length=256), nullable=True))


def downgrade() -> None:
    """Remove the webhook secret reference."""
    op.drop_column("bot_connections", "webhook_secret_name")
