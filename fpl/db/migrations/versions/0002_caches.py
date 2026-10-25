"""Caches: optimiser results and FPL API responses (#83).

Revision ID: 0002
Revises: 0001
Created: 2026-10-25 16:38:48.799857
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "upstream_responses",
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fetched_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("path", name=op.f("pk_upstream_responses")),
    )
    op.create_table(
        "optimise_results",
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("gameweek", sa.SmallInteger(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_optimise_results_request_hash")
        ),
        sa.ForeignKeyConstraint(
            ["season", "gameweek", "model_version"],
            [
                "prediction_entries.season",
                "prediction_entries.gameweek",
                "prediction_entries.model_version",
            ],
            name=op.f("fk_optimise_results_season_gameweek_model_version_prediction_entries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("request_hash", name=op.f("pk_optimise_results")),
    )


def downgrade() -> None:
    op.drop_table("optimise_results")
    op.drop_table("upstream_responses")
