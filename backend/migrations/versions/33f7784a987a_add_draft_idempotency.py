"""Add durable idempotency metadata to transaction drafts."""

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "33f7784a987a"
down_revision: str | None = "9bdd0c864997"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add request identity fields while preserving any existing draft rows."""
    op.add_column("drafts", sa.Column("idempotency_key", sa.String(128), nullable=True))
    op.add_column("drafts", sa.Column("request_hash", sa.String(64), nullable=True))
    drafts = sa.table(
        "drafts",
        sa.column("id", sa.Uuid()),
        sa.column("idempotency_key", sa.String(128)),
        sa.column("request_hash", sa.String(64)),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(drafts.c.idempotency_key, drafts.c.id)):
        draft_id = row.id
        connection.execute(
            drafts.update()
            .where(drafts.c.id == draft_id)
            .values(
                idempotency_key=f"legacy-{draft_id}",
                request_hash=hashlib.sha256(str(draft_id).encode("ascii")).hexdigest(),
            )
        )
    op.alter_column("drafts", "idempotency_key", nullable=False)
    op.alter_column("drafts", "request_hash", nullable=False)
    op.create_unique_constraint("uq_draft_user_idempotency", "drafts", ["user_id", "idempotency_key"])
    op.create_check_constraint("idempotency_key_length", "drafts", "length(idempotency_key) BETWEEN 1 AND 128")
    op.create_check_constraint("request_hash_length", "drafts", "length(request_hash) = 64")


def downgrade() -> None:
    """Remove idempotency metadata."""
    op.drop_constraint("request_hash_length", "drafts", type_="check")
    op.drop_constraint("idempotency_key_length", "drafts", type_="check")
    op.drop_constraint("uq_draft_user_idempotency", "drafts", type_="unique")
    op.drop_column("drafts", "request_hash")
    op.drop_column("drafts", "idempotency_key")
