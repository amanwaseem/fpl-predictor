"""The schema, as SQLAlchemy models. Alembic migrations are generated from it.

Three groups of tables:

- **Football**: seasons, teams, players, gameweeks, fixtures and per-fixture
  player stats, loaded from snapshots (#78) and past seasons (#79).
- **Snapshots**: one row per snapshot loaded, so a stats row can say where it
  came from.
- **The log**: prediction entries and scores, a projection of `predictions/`
  and `scores/` (#80). These copy what was committed, as it was written, so
  they hold names and clubs as text rather than keys into the football tables.
- **Caches** (#83): the optimiser's results and the FPL API's responses, kept
  by the API so a repeated request is cheap. Disposable: nothing is lost if
  they are emptied, and a deploy's projection of the log empties the results.

Keys are FPL's own ids, qualified by season wherever FPL reuses them: element
and team ids restart every season, and fixture ids restart with them. The
`code` columns are FPL's ids for a person or a club across seasons.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INT4RANGE, JSONB, ExcludeConstraint, Range
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from fpl.log.contract import POSITIONS, STATUSES

# Constraint names are part of the schema: migrations refer to them by name, so
# they are generated predictably rather than left to Postgres.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def one_of(column, values):
    """A CHECK that column is one of values. Sorted, so a set gives stable SQL."""
    return f"{column} IN ({', '.join(repr(v) for v in sorted(values))})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# --- Football ---------------------------------------------------------------


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (CheckConstraint(r"season ~ '^\d{4}/\d{2}$'", name="format"),)

    # FPL's own spelling, as in element-summary's history_past: "2026/27".
    season: Mapped[str] = mapped_column(Text, primary_key=True)


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("season", "code"),
        UniqueConstraint("season", "short_name"),
    )

    season: Mapped[str] = mapped_column(ForeignKey("seasons.season"), primary_key=True)
    team_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    short_name: Mapped[str] = mapped_column(Text)


class Player(Base):
    """A person, across seasons. FPL's element id is per season; code is not."""

    __tablename__ = "players"

    # FPL assigns codes; autoincrement would make this a SERIAL.
    code: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    first_name: Mapped[str] = mapped_column(Text)
    second_name: Mapped[str] = mapped_column(Text)


class SeasonPlayer(Base):
    __tablename__ = "season_players"
    __table_args__ = (
        UniqueConstraint("season", "player_code"),
        CheckConstraint(one_of("position", POSITIONS), name="position"),
    )

    season: Mapped[str] = mapped_column(ForeignKey("seasons.season"), primary_key=True)
    element_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_code: Mapped[int] = mapped_column(ForeignKey("players.code"))
    web_name: Mapped[str] = mapped_column(Text)
    position: Mapped[str] = mapped_column(Text)


class PlayerClub(Base):
    """Which club a player was at, for a range of gameweeks (ADR 0013).

    The bootstrap gives only a player's current club, so a mid-season move
    shows up as a new spell starting at the first gameweek after it. Spells of
    one player cannot overlap.
    """

    __tablename__ = "player_clubs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["season", "element_id"], ["season_players.season", "season_players.element_id"]
        ),
        ForeignKeyConstraint(["season", "team_id"], ["teams.season", "teams.team_id"]),
        # lower() is null for an unbounded range, and a null CHECK passes, so
        # lower_inf is refused explicitly: every spell starts at a gameweek.
        CheckConstraint(
            "NOT isempty(gameweeks) AND NOT lower_inf(gameweeks) AND lower(gameweeks) >= 1",
            name="gameweeks",
        ),
        ExcludeConstraint(
            ("season", "="),
            ("element_id", "="),
            ("gameweeks", "&&"),
            using="gist",
            name="ex_player_clubs_one_club_at_a_time",
        ),
    )

    season: Mapped[str] = mapped_column(Text, primary_key=True)
    element_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Half-open, as Postgres normalises integer ranges: [6,39) is GW6 onwards.
    gameweeks: Mapped[Range[int]] = mapped_column(INT4RANGE, primary_key=True)
    team_id: Mapped[int] = mapped_column(SmallInteger)


class Gameweek(Base):
    __tablename__ = "gameweeks"
    __table_args__ = (CheckConstraint("gameweek BETWEEN 1 AND 38", name="range"),)

    season: Mapped[str] = mapped_column(ForeignKey("seasons.season"), primary_key=True)
    gameweek: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    deadline_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # Bonus is provisional until this is true, so nothing is scored before it.
    data_checked: Mapped[bool] = mapped_column(Boolean, server_default="false")


class Fixture(Base):
    __tablename__ = "fixtures"
    __table_args__ = (
        ForeignKeyConstraint(["season", "gameweek"], ["gameweeks.season", "gameweeks.gameweek"]),
        ForeignKeyConstraint(["season", "team_h"], ["teams.season", "teams.team_id"]),
        ForeignKeyConstraint(["season", "team_a"], ["teams.season", "teams.team_id"]),
        CheckConstraint("team_h <> team_a", name="two_teams"),
        CheckConstraint(
            "(team_h_score IS NULL) = (team_a_score IS NULL)"
            " AND coalesce(team_h_score, 0) >= 0 AND coalesce(team_a_score, 0) >= 0",
            name="score",
        ),
        Index("ix_fixtures_gameweek", "season", "gameweek"),
    )

    # No key into seasons of its own: the team keys already carry the season.
    season: Mapped[str] = mapped_column(Text, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Null while a postponed fixture waits to be rescheduled.
    gameweek: Mapped[int | None] = mapped_column(SmallInteger)
    kickoff_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    team_h: Mapped[int] = mapped_column(SmallInteger)
    team_a: Mapped[int] = mapped_column(SmallInteger)
    team_h_score: Mapped[int | None] = mapped_column(SmallInteger)
    team_a_score: Mapped[int | None] = mapped_column(SmallInteger)
    finished: Mapped[bool] = mapped_column(Boolean, server_default="false")


class PlayerFixtureStats(Base):
    """One player's line in one fixture: an element-summary history row.

    Columns FPL added in later seasons are nullable, because past seasons (#79)
    do not have them: expected goals from 2022/23, defensive contribution from
    2025/26.
    """

    __tablename__ = "player_fixture_stats"
    __table_args__ = (
        ForeignKeyConstraint(
            ["season", "element_id"], ["season_players.season", "season_players.element_id"]
        ),
        ForeignKeyConstraint(
            ["season", "fixture_id"], ["fixtures.season", "fixtures.fixture_id"]
        ),
        CheckConstraint("minutes >= 0", name="minutes"),
        Index("ix_player_fixture_stats_fixture", "season", "fixture_id"),
        Index("ix_player_fixture_stats_snapshot", "snapshot_id"),
    )

    season: Mapped[str] = mapped_column(Text, primary_key=True)
    element_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The snapshot the row was loaded from. Null for past seasons, which come
    # from another source (#79).
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("snapshots.snapshot_id"))

    was_home: Mapped[bool] = mapped_column(Boolean)
    opponent_team: Mapped[int] = mapped_column(SmallInteger)
    minutes: Mapped[int] = mapped_column(SmallInteger)
    starts: Mapped[int | None] = mapped_column(SmallInteger)
    total_points: Mapped[int] = mapped_column(SmallInteger)

    goals_scored: Mapped[int] = mapped_column(SmallInteger)
    assists: Mapped[int] = mapped_column(SmallInteger)
    clean_sheets: Mapped[int] = mapped_column(SmallInteger)
    goals_conceded: Mapped[int] = mapped_column(SmallInteger)
    own_goals: Mapped[int] = mapped_column(SmallInteger)
    penalties_saved: Mapped[int] = mapped_column(SmallInteger)
    penalties_missed: Mapped[int] = mapped_column(SmallInteger)
    yellow_cards: Mapped[int] = mapped_column(SmallInteger)
    red_cards: Mapped[int] = mapped_column(SmallInteger)
    saves: Mapped[int] = mapped_column(SmallInteger)
    bonus: Mapped[int] = mapped_column(SmallInteger)
    bps: Mapped[int] = mapped_column(SmallInteger)

    expected_goals: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    expected_assists: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    expected_goal_involvements: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    expected_goals_conceded: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    influence: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    creativity: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    threat: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    ict_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))

    defensive_contribution: Mapped[int | None] = mapped_column(SmallInteger)
    clearances_blocks_interceptions: Mapped[int | None] = mapped_column(SmallInteger)
    recoveries: Mapped[int | None] = mapped_column(SmallInteger)
    tackles: Mapped[int | None] = mapped_column(SmallInteger)

    # Price in tenths of a million, as the API gives it: 100 is £10.0m.
    value: Mapped[int] = mapped_column(SmallInteger)
    selected: Mapped[int | None] = mapped_column(Integer)
    transfers_in: Mapped[int | None] = mapped_column(Integer)
    transfers_out: Mapped[int | None] = mapped_column(Integer)
    transfers_balance: Mapped[int | None] = mapped_column(Integer)


# --- Snapshots --------------------------------------------------------------


class Snapshot(Base):
    """A snapshot that has been loaded, with what its manifest says it holds."""

    __tablename__ = "snapshots"
    __table_args__ = (
        CheckConstraint(r"snapshot_id ~ '^\d{8}T\d{6}Z$'", name="format"),
    )

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    season: Mapped[str] = mapped_column(ForeignKey("seasons.season"))
    completed_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    element_count: Mapped[int] = mapped_column(Integer)
    has_players: Mapped[bool] = mapped_column(Boolean)
    live_gameweeks: Mapped[list[int]] = mapped_column(
        ARRAY(SmallInteger), server_default="{}"
    )
    loaded_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# --- The log ----------------------------------------------------------------


class PredictionEntry(Base):
    """One committed file in predictions/: a model's entry for a gameweek.

    The provenance columns are the same on every row of a file, so they are
    stored once here. snapshot_id is not a foreign key: snapshots are not
    shared, so an entry's snapshot is often not loaded on this machine.
    """

    __tablename__ = "prediction_entries"
    __table_args__ = (
        CheckConstraint(r"snapshot_id ~ '^\d{8}T\d{6}Z$'", name="snapshot_id"),
        CheckConstraint("model_version <> '' AND strpos(model_version, '_') = 0",
                        name="model_version"),
        # An entry made after its deadline is not evidence of anything (SPEC 5).
        CheckConstraint("generated_at_utc < deadline_utc", name="before_deadline"),
    )

    season: Mapped[str] = mapped_column(ForeignKey("seasons.season"), primary_key=True)
    gameweek: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    model_version: Mapped[str] = mapped_column(Text, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(Text)
    generated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Prediction(Base):
    """One row of a committed entry, as written."""

    __tablename__ = "predictions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["season", "gameweek", "model_version"],
            ["prediction_entries.season", "prediction_entries.gameweek",
             "prediction_entries.model_version"],
            ondelete="CASCADE",
        ),
        CheckConstraint(one_of("position", POSITIONS), name="position"),
        CheckConstraint(one_of("status", STATUSES), name="status"),
        CheckConstraint("price >= 0 AND expected_minutes >= 0 AND n_fixtures >= 0",
                        name="non_negative"),
        Index("ix_predictions_player", "season", "player_id"),
    )

    season: Mapped[str] = mapped_column(Text, primary_key=True)
    gameweek: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    model_version: Mapped[str] = mapped_column(Text, primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    web_name: Mapped[str] = mapped_column(Text)
    team: Mapped[str] = mapped_column(Text)
    position: Mapped[str] = mapped_column(Text)
    # Unconstrained numeric: a copy of the log keeps every digit it was written
    # with, so it can be reconciled against predictions/ exactly (#80).
    price: Mapped[Decimal] = mapped_column(Numeric)
    # Can be negative: a red card or an own goal scores below zero.
    predicted_points: Mapped[Decimal] = mapped_column(Numeric)
    expected_minutes: Mapped[Decimal] = mapped_column(Numeric)
    points_per_90: Mapped[Decimal] = mapped_column(Numeric)
    n_fixtures: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(Text)


class ScoreEntry(Base):
    """One file in scores/: an entry scored against what happened.

    The headline metrics are columns so they can be queried and compared; the
    whole score file, breakdowns included, is kept as `summary`.
    """

    __tablename__ = "score_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["season", "gameweek", "model_version"],
            ["prediction_entries.season", "prediction_entries.gameweek",
             "prediction_entries.model_version"],
            ondelete="CASCADE",
        ),
        CheckConstraint(r"actuals_snapshot_id ~ '^\d{8}T\d{6}Z$'", name="actuals_snapshot_id"),
    )

    season: Mapped[str] = mapped_column(Text, primary_key=True)
    gameweek: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    model_version: Mapped[str] = mapped_column(Text, primary_key=True)
    harness_version: Mapped[str] = mapped_column(Text)
    actuals_snapshot_id: Mapped[str] = mapped_column(Text)
    n: Mapped[int] = mapped_column(Integer)
    mae: Mapped[float] = mapped_column(Float)
    rmse: Mapped[float] = mapped_column(Float)
    bias: Mapped[float] = mapped_column(Float)
    spearman: Mapped[float | None] = mapped_column(Float)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB)


class PlayerScore(Base):
    """One player's prediction against their actual points: a row of scores/*.csv."""

    __tablename__ = "player_scores"
    __table_args__ = (
        ForeignKeyConstraint(
            ["season", "gameweek", "model_version"],
            ["score_entries.season", "score_entries.gameweek", "score_entries.model_version"],
            ondelete="CASCADE",
        ),
        Index("ix_player_scores_player", "season", "player_id"),
    )

    season: Mapped[str] = mapped_column(Text, primary_key=True)
    gameweek: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    model_version: Mapped[str] = mapped_column(Text, primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    predicted_points: Mapped[Decimal] = mapped_column(Numeric)
    actual_points: Mapped[int] = mapped_column(SmallInteger)
    expected_minutes: Mapped[Decimal] = mapped_column(Numeric)
    actual_minutes: Mapped[int] = mapped_column(SmallInteger)
    # Derived by Postgres, so it cannot disagree with the two columns it is from.
    error: Mapped[Decimal] = mapped_column(
        Numeric, Computed("predicted_points - actual_points", persisted=True)
    )


# --- Caches -----------------------------------------------------------------


class OptimiseResult(Base):
    """One optimiser result, keyed by a hash of everything that decides it (#83).

    Tied to the entry it was solved from, so the projection, which replaces
    every entry on each deploy, empties this too: a result can never outlive
    the predictions it came from, or the code that solved it.
    """

    __tablename__ = "optimise_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["season", "gameweek", "model_version"],
            ["prediction_entries.season", "prediction_entries.gameweek",
             "prediction_entries.model_version"],
            ondelete="CASCADE",
        ),
        CheckConstraint(r"request_hash ~ '^[0-9a-f]{64}$'", name="request_hash"),
    )

    request_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    season: Mapped[str] = mapped_column(Text)
    gameweek: Mapped[int] = mapped_column(SmallInteger)
    model_version: Mapped[str] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UpstreamResponse(Base):
    """A response from the FPL API, kept for a few minutes (#83).

    A 404 is kept too, with no body, so a team id that does not exist costs
    FPL one request per few minutes, not one per ask.
    """

    __tablename__ = "upstream_responses"
    __table_args__ = (
        CheckConstraint("status IN (200, 404)", name="status"),
        Index("ix_upstream_responses_fetched_at_utc", "fetched_at_utc"),
    )

    path: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[int] = mapped_column(SmallInteger)
    body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    fetched_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
