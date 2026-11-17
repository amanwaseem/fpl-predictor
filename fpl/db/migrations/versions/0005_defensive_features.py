"""Defensive-contribution features: features-v2's columns on player_gameweek_features (#174).

Revision ID: 0005
Revises: 0004
Created: 2026-11-17 18:00:00.000000

Nullable, and filled by the next fpl-features run: the feature version is part
of each season's fingerprint, so every season is rebuilt.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ("defensive_rule", sa.SmallInteger()),
    ("defensive_per90_5", sa.Float()),
    ("defensive_per90_season", sa.Float()),
    ("defensive_hits_last5", sa.SmallInteger()),
)


def upgrade() -> None:
    for name, kind in COLUMNS:
        op.add_column("player_gameweek_features", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(COLUMNS):
        op.drop_column("player_gameweek_features", name)
