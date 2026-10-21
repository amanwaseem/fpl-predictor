"""Initial schema: football, snapshots and the log (#77).

Revision ID: 0001
Revises:
Created: 2026-10-21 14:02:07.541741
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # player_clubs' exclusion constraint compares season and element_id with =
    # inside a GiST index, which needs btree_gist's operator classes.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "players",
        sa.Column("code", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("second_name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_players")),
    )
    op.create_table(
        "seasons",
        sa.Column("season", sa.Text(), nullable=False),
        sa.CheckConstraint("season ~ '^\\d{4}/\\d{2}$'", name=op.f("ck_seasons_format")),
        sa.PrimaryKeyConstraint("season", name=op.f("pk_seasons")),
    )
    op.create_table(
        "gameweeks",
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("gameweek", sa.SmallInteger(), nullable=False),
        sa.Column("deadline_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("data_checked", sa.Boolean(), server_default="false", nullable=False),
        sa.CheckConstraint("gameweek BETWEEN 1 AND 38", name=op.f("ck_gameweeks_range")),
        sa.ForeignKeyConstraint(
            ["season"], ["seasons.season"], name=op.f("fk_gameweeks_season_seasons")
        ),
        sa.PrimaryKeyConstraint("season", "gameweek", name=op.f("pk_gameweeks")),
    )
    op.create_table(
        "prediction_entries",
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("gameweek", sa.SmallInteger(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("generated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_utc", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "model_version <> '' AND strpos(model_version, '_') = 0",
            name=op.f("ck_prediction_entries_model_version"),
        ),
        sa.CheckConstraint(
            "snapshot_id ~ '^\\d{8}T\\d{6}Z$'", name=op.f("ck_prediction_entries_snapshot_id")
        ),
        sa.CheckConstraint(
            "generated_at_utc < deadline_utc", name=op.f("ck_prediction_entries_before_deadline")
        ),
        sa.ForeignKeyConstraint(
            ["season"], ["seasons.season"], name=op.f("fk_prediction_entries_season_seasons")
        ),
        sa.PrimaryKeyConstraint(
            "season", "gameweek", "model_version", name=op.f("pk_prediction_entries")
        ),
    )
    op.create_table(
        "season_players",
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("element_id", sa.Integer(), nullable=False),
        sa.Column("player_code", sa.Integer(), nullable=False),
        sa.Column("web_name", sa.Text(), nullable=False),
        sa.Column("position", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "position IN ('DEF', 'FWD', 'GKP', 'MID')", name=op.f("ck_season_players_position")
        ),
        sa.ForeignKeyConstraint(
            ["player_code"], ["players.code"], name=op.f("fk_season_players_player_code_players")
        ),
        sa.ForeignKeyConstraint(
            ["season"], ["seasons.season"], name=op.f("fk_season_players_season_seasons")
        ),
        sa.PrimaryKeyConstraint("season", "element_id", name=op.f("pk_season_players")),
        sa.UniqueConstraint(
            "season", "player_code", name=op.f("uq_season_players_season_player_code")
        ),
    )
    op.create_table(
        "snapshots",
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("completed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("element_count", sa.Integer(), nullable=False),
        sa.Column("has_players", sa.Boolean(), nullable=False),
        sa.Column(
            "live_gameweeks", sa.ARRAY(sa.SmallInteger()), server_default="{}", nullable=False
        ),
        sa.Column(
            "loaded_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("snapshot_id ~ '^\\d{8}T\\d{6}Z$'", name=op.f("ck_snapshots_format")),
        sa.ForeignKeyConstraint(
            ["season"], ["seasons.season"], name=op.f("fk_snapshots_season_seasons")
        ),
        sa.PrimaryKeyConstraint("snapshot_id", name=op.f("pk_snapshots")),
    )
    op.create_table(
        "teams",
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("team_id", sa.SmallInteger(), nullable=False),
        sa.Column("code", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("short_name", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["season"], ["seasons.season"], name=op.f("fk_teams_season_seasons")
        ),
        sa.PrimaryKeyConstraint("season", "team_id", name=op.f("pk_teams")),
        sa.UniqueConstraint("season", "code", name=op.f("uq_teams_season_code")),
        sa.UniqueConstraint("season", "short_name", name=op.f("uq_teams_season_short_name")),
    )
    op.create_table(
        "fixtures",
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("fixture_id", sa.Integer(), nullable=False),
        sa.Column("gameweek", sa.SmallInteger(), nullable=True),
        sa.Column("kickoff_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("team_h", sa.SmallInteger(), nullable=False),
        sa.Column("team_a", sa.SmallInteger(), nullable=False),
        sa.Column("team_h_score", sa.SmallInteger(), nullable=True),
        sa.Column("team_a_score", sa.SmallInteger(), nullable=True),
        sa.Column("finished", sa.Boolean(), server_default="false", nullable=False),
        sa.CheckConstraint(
            "(team_h_score IS NULL) = (team_a_score IS NULL) AND coalesce(team_h_score, 0) >= 0 AND coalesce(team_a_score, 0) >= 0",
            name=op.f("ck_fixtures_score"),
        ),
        sa.CheckConstraint("team_h <> team_a", name=op.f("ck_fixtures_two_teams")),
        sa.ForeignKeyConstraint(
            ["season", "gameweek"],
            ["gameweeks.season", "gameweeks.gameweek"],
            name=op.f("fk_fixtures_season_gameweek_gameweeks"),
        ),
        sa.ForeignKeyConstraint(
            ["season", "team_a"],
            ["teams.season", "teams.team_id"],
            name=op.f("fk_fixtures_season_team_a_teams"),
        ),
        sa.ForeignKeyConstraint(
            ["season", "team_h"],
            ["teams.season", "teams.team_id"],
            name=op.f("fk_fixtures_season_team_h_teams"),
        ),
        sa.PrimaryKeyConstraint("season", "fixture_id", name=op.f("pk_fixtures")),
    )
    op.create_index("ix_fixtures_gameweek", "fixtures", ["season", "gameweek"], unique=False)
    op.create_table(
        "player_clubs",
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("element_id", sa.Integer(), nullable=False),
        sa.Column("gameweeks", postgresql.INT4RANGE(), nullable=False),
        sa.Column("team_id", sa.SmallInteger(), nullable=False),
        postgresql.ExcludeConstraint(
            (sa.column("season"), "="),
            (sa.column("element_id"), "="),
            (sa.column("gameweeks"), "&&"),
            using="gist",
            name="ex_player_clubs_one_club_at_a_time",
        ),
        sa.CheckConstraint(
            "NOT isempty(gameweeks) AND NOT lower_inf(gameweeks) AND lower(gameweeks) >= 1",
            name=op.f("ck_player_clubs_gameweeks"),
        ),
        sa.ForeignKeyConstraint(
            ["season", "element_id"],
            ["season_players.season", "season_players.element_id"],
            name=op.f("fk_player_clubs_season_element_id_season_players"),
        ),
        sa.ForeignKeyConstraint(
            ["season", "team_id"],
            ["teams.season", "teams.team_id"],
            name=op.f("fk_player_clubs_season_team_id_teams"),
        ),
        sa.PrimaryKeyConstraint("season", "element_id", "gameweeks", name=op.f("pk_player_clubs")),
    )
    op.create_table(
        "predictions",
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("gameweek", sa.SmallInteger(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("web_name", sa.Text(), nullable=False),
        sa.Column("team", sa.Text(), nullable=False),
        sa.Column("position", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("predicted_points", sa.Numeric(), nullable=False),
        sa.Column("expected_minutes", sa.Numeric(), nullable=False),
        sa.Column("points_per_90", sa.Numeric(), nullable=False),
        sa.Column("n_fixtures", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "position IN ('DEF', 'FWD', 'GKP', 'MID')", name=op.f("ck_predictions_position")
        ),
        sa.CheckConstraint(
            "status IN ('a', 'd', 'i', 'n', 's', 'u')", name=op.f("ck_predictions_status")
        ),
        sa.CheckConstraint(
            "price >= 0 AND expected_minutes >= 0 AND n_fixtures >= 0",
            name=op.f("ck_predictions_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["season", "gameweek", "model_version"],
            [
                "prediction_entries.season",
                "prediction_entries.gameweek",
                "prediction_entries.model_version",
            ],
            name=op.f("fk_predictions_season_gameweek_model_version_prediction_entries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "season", "gameweek", "model_version", "player_id", name=op.f("pk_predictions")
        ),
    )
    op.create_index("ix_predictions_player", "predictions", ["season", "player_id"], unique=False)
    op.create_table(
        "score_entries",
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("gameweek", sa.SmallInteger(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("harness_version", sa.Text(), nullable=False),
        sa.Column("actuals_snapshot_id", sa.Text(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("mae", sa.Float(), nullable=False),
        sa.Column("rmse", sa.Float(), nullable=False),
        sa.Column("bias", sa.Float(), nullable=False),
        sa.Column("spearman", sa.Float(), nullable=True),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "actuals_snapshot_id ~ '^\\d{8}T\\d{6}Z$'",
            name=op.f("ck_score_entries_actuals_snapshot_id"),
        ),
        sa.ForeignKeyConstraint(
            ["season", "gameweek", "model_version"],
            [
                "prediction_entries.season",
                "prediction_entries.gameweek",
                "prediction_entries.model_version",
            ],
            name=op.f("fk_score_entries_season_gameweek_model_version_prediction_entries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "season", "gameweek", "model_version", name=op.f("pk_score_entries")
        ),
    )
    op.create_table(
        "player_fixture_stats",
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("element_id", sa.Integer(), nullable=False),
        sa.Column("fixture_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Text(), nullable=True),
        sa.Column("was_home", sa.Boolean(), nullable=False),
        sa.Column("opponent_team", sa.SmallInteger(), nullable=False),
        sa.Column("minutes", sa.SmallInteger(), nullable=False),
        sa.Column("starts", sa.SmallInteger(), nullable=True),
        sa.Column("total_points", sa.SmallInteger(), nullable=False),
        sa.Column("goals_scored", sa.SmallInteger(), nullable=False),
        sa.Column("assists", sa.SmallInteger(), nullable=False),
        sa.Column("clean_sheets", sa.SmallInteger(), nullable=False),
        sa.Column("goals_conceded", sa.SmallInteger(), nullable=False),
        sa.Column("own_goals", sa.SmallInteger(), nullable=False),
        sa.Column("penalties_saved", sa.SmallInteger(), nullable=False),
        sa.Column("penalties_missed", sa.SmallInteger(), nullable=False),
        sa.Column("yellow_cards", sa.SmallInteger(), nullable=False),
        sa.Column("red_cards", sa.SmallInteger(), nullable=False),
        sa.Column("saves", sa.SmallInteger(), nullable=False),
        sa.Column("bonus", sa.SmallInteger(), nullable=False),
        sa.Column("bps", sa.SmallInteger(), nullable=False),
        sa.Column("expected_goals", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("expected_assists", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("expected_goal_involvements", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("expected_goals_conceded", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("influence", sa.Numeric(precision=5, scale=1), nullable=True),
        sa.Column("creativity", sa.Numeric(precision=5, scale=1), nullable=True),
        sa.Column("threat", sa.Numeric(precision=5, scale=1), nullable=True),
        sa.Column("ict_index", sa.Numeric(precision=5, scale=1), nullable=True),
        sa.Column("defensive_contribution", sa.SmallInteger(), nullable=True),
        sa.Column("clearances_blocks_interceptions", sa.SmallInteger(), nullable=True),
        sa.Column("recoveries", sa.SmallInteger(), nullable=True),
        sa.Column("tackles", sa.SmallInteger(), nullable=True),
        sa.Column("value", sa.SmallInteger(), nullable=False),
        sa.Column("selected", sa.Integer(), nullable=True),
        sa.Column("transfers_in", sa.Integer(), nullable=True),
        sa.Column("transfers_out", sa.Integer(), nullable=True),
        sa.Column("transfers_balance", sa.Integer(), nullable=True),
        sa.CheckConstraint("minutes >= 0", name=op.f("ck_player_fixture_stats_minutes")),
        sa.ForeignKeyConstraint(
            ["season", "element_id"],
            ["season_players.season", "season_players.element_id"],
            name=op.f("fk_player_fixture_stats_season_element_id_season_players"),
        ),
        sa.ForeignKeyConstraint(
            ["season", "fixture_id"],
            ["fixtures.season", "fixtures.fixture_id"],
            name=op.f("fk_player_fixture_stats_season_fixture_id_fixtures"),
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.snapshot_id"],
            name=op.f("fk_player_fixture_stats_snapshot_id_snapshots"),
        ),
        sa.PrimaryKeyConstraint(
            "season", "element_id", "fixture_id", name=op.f("pk_player_fixture_stats")
        ),
    )
    op.create_index(
        "ix_player_fixture_stats_fixture",
        "player_fixture_stats",
        ["season", "fixture_id"],
        unique=False,
    )
    op.create_index(
        "ix_player_fixture_stats_snapshot", "player_fixture_stats", ["snapshot_id"], unique=False
    )
    op.create_table(
        "player_scores",
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("gameweek", sa.SmallInteger(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("predicted_points", sa.Numeric(), nullable=False),
        sa.Column("actual_points", sa.SmallInteger(), nullable=False),
        sa.Column("expected_minutes", sa.Numeric(), nullable=False),
        sa.Column("actual_minutes", sa.SmallInteger(), nullable=False),
        sa.Column(
            "error",
            sa.Numeric(),
            sa.Computed("predicted_points - actual_points", persisted=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["season", "gameweek", "model_version"],
            ["score_entries.season", "score_entries.gameweek", "score_entries.model_version"],
            name=op.f("fk_player_scores_season_gameweek_model_version_score_entries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "season", "gameweek", "model_version", "player_id", name=op.f("pk_player_scores")
        ),
    )
    op.create_index(
        "ix_player_scores_player", "player_scores", ["season", "player_id"], unique=False
    )


def downgrade() -> None:
    # btree_gist stays: it belongs to the database, may predate this migration,
    # and something else may use it.
    op.drop_index("ix_player_scores_player", table_name="player_scores")
    op.drop_table("player_scores")
    op.drop_index("ix_player_fixture_stats_snapshot", table_name="player_fixture_stats")
    op.drop_index("ix_player_fixture_stats_fixture", table_name="player_fixture_stats")
    op.drop_table("player_fixture_stats")
    op.drop_table("score_entries")
    op.drop_index("ix_predictions_player", table_name="predictions")
    op.drop_table("predictions")
    op.drop_table("player_clubs")
    op.drop_index("ix_fixtures_gameweek", table_name="fixtures")
    op.drop_table("fixtures")
    op.drop_table("teams")
    op.drop_table("snapshots")
    op.drop_table("season_players")
    op.drop_table("prediction_entries")
    op.drop_table("gameweeks")
    op.drop_table("seasons")
    op.drop_table("players")
