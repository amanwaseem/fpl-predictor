"""Feature table: player_gameweek_features and feature_builds (#84).

Revision ID: 0003
Revises: 0002
Created: 2026-10-25 19:32:50.975368
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feature_builds",
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("feature_version", sa.Text(), nullable=False),
        sa.Column("inputs", sa.Text(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column(
            "built_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["season"], ["seasons.season"], name=op.f("fk_feature_builds_season_seasons")
        ),
        sa.PrimaryKeyConstraint("season", name=op.f("pk_feature_builds")),
    )
    op.create_table(
        "player_gameweek_features",
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("gameweek", sa.SmallInteger(), nullable=False),
        sa.Column("element_id", sa.Integer(), nullable=False),
        sa.Column("feature_version", sa.Text(), nullable=False),
        sa.Column("player_code", sa.Integer(), nullable=False),
        sa.Column("position", sa.Text(), nullable=False),
        sa.Column("team_id", sa.SmallInteger(), nullable=False),
        sa.Column("n_fixtures", sa.SmallInteger(), nullable=False),
        sa.Column("n_home", sa.SmallInteger(), nullable=False),
        sa.Column("price", sa.SmallInteger(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("minutes_last1", sa.SmallInteger(), nullable=True),
        sa.Column("minutes_mean3", sa.Float(), nullable=True),
        sa.Column("minutes_mean5", sa.Float(), nullable=True),
        sa.Column("appearances_last5", sa.SmallInteger(), nullable=False),
        sa.Column("sixty_last5", sa.SmallInteger(), nullable=False),
        sa.Column("season_rounds", sa.SmallInteger(), nullable=False),
        sa.Column("season_minutes", sa.Integer(), nullable=False),
        sa.Column("season_points", sa.Integer(), nullable=False),
        sa.Column("points_per90_3", sa.Float(), nullable=True),
        sa.Column("points_per90_5", sa.Float(), nullable=True),
        sa.Column("points_per90_season", sa.Float(), nullable=True),
        sa.Column("xg_per90_5", sa.Float(), nullable=True),
        sa.Column("xa_per90_5", sa.Float(), nullable=True),
        sa.Column("xgi_per90_season", sa.Float(), nullable=True),
        sa.Column("bonus_per90_season", sa.Float(), nullable=True),
        sa.Column("last_season_minutes", sa.Integer(), nullable=True),
        sa.Column("last_season_points_per90", sa.Float(), nullable=True),
        sa.Column("team_attack", sa.Float(), nullable=True),
        sa.Column("team_defence", sa.Float(), nullable=True),
        sa.Column("opponent_attack", sa.Float(), nullable=True),
        sa.Column("opponent_defence", sa.Float(), nullable=True),
        sa.Column("expected_goals_for", sa.Float(), nullable=True),
        sa.Column("expected_goals_against", sa.Float(), nullable=True),
        sa.Column("expected_clean_sheets", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "position IN ('DEF', 'FWD', 'GKP', 'MID')",
            name=op.f("ck_player_gameweek_features_position"),
        ),
        sa.ForeignKeyConstraint(
            ["season", "element_id"],
            ["season_players.season", "season_players.element_id"],
            name=op.f("fk_player_gameweek_features_season_element_id_season_players"),
        ),
        sa.ForeignKeyConstraint(
            ["season", "gameweek"],
            ["gameweeks.season", "gameweeks.gameweek"],
            name=op.f("fk_player_gameweek_features_season_gameweek_gameweeks"),
        ),
        sa.PrimaryKeyConstraint(
            "season", "gameweek", "element_id", name=op.f("pk_player_gameweek_features")
        ),
    )
    op.create_index(
        "ix_player_gameweek_features_player",
        "player_gameweek_features",
        ["player_code"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_player_gameweek_features_player", table_name="player_gameweek_features")
    op.drop_table("player_gameweek_features")
    op.drop_table("feature_builds")
