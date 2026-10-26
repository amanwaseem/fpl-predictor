"""Ingest checks: when the ingest last compared FPL with the database (#86).

Revision ID: 0004
Revises: 0003
Created: 2026-10-26 00:43:47.431513
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingest_checks",
        sa.Column(
            "checked_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("compared_snapshot_id", sa.Text(), nullable=False),
        sa.Column("load_needed", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("checked_at_utc", name=op.f("pk_ingest_checks")),
    )


def downgrade() -> None:
    op.drop_table("ingest_checks")
