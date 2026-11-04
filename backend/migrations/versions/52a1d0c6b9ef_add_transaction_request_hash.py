"""Persist transaction confirmation request fingerprints."""

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "52a1d0c6b9ef"
down_revision: str | None = "33f7784a987a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add confirmation fingerprints and constraints with safe backfill."""
    op.add_column("transactions", sa.Column("request_hash", sa.String(64), nullable=True))
    transactions = sa.table(
        "transactions",
        sa.column("id", sa.Uuid()),
        sa.column("request_hash", sa.String(64)),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(transactions.c.id)):
        connection.execute(
            transactions.update()
            .where(transactions.c.id == row.id)
            .values(request_hash=hashlib.sha256(str(row.id).encode("ascii")).hexdigest())
        )
    op.alter_column("transactions", "request_hash", nullable=False)
    op.create_check_constraint("request_hash_length", "transactions", "length(request_hash) = 64")
    op.create_check_constraint("idempotency_key_length", "transactions", "length(idempotency_key) BETWEEN 1 AND 128")


def downgrade() -> None:
    """Remove confirmation request fingerprints."""
    op.drop_constraint("idempotency_key_length", "transactions", type_="check")
    op.drop_constraint("request_hash_length", "transactions", type_="check")
    op.drop_column("transactions", "request_hash")
