"""What each route returns. These models are the API's contract, and its OpenAPI.

Numbers the database holds as exact decimals are served as JSON numbers: the
log writes at most two decimal places, which a float carries exactly enough.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class Gameweek(BaseModel):
    gameweek: int
    deadline_utc: datetime
    finished: bool
    data_checked: bool
    models_logged: list[str]


class GameweekList(BaseModel):
    season: str
    gameweeks: list[Gameweek]


class Fixture(BaseModel):
    fixture_id: int
    kickoff_utc: datetime | None
    home: str
    away: str
    home_score: int | None
    away_score: int | None
    finished: bool


class Metrics(BaseModel):
    n: int
    mae: float
    rmse: float
    bias: float
    spearman: float | None


class Entry(BaseModel):
    """One model's committed entry for a gameweek, and its score once settled."""

    model_version: str
    snapshot_id: str
    generated_at_utc: datetime
    deadline_utc: datetime
    score: Metrics | None


class GameweekDetail(Gameweek):
    season: str
    fixtures: list[Fixture]
    entries: list[Entry]


class Prediction(BaseModel):
    player_id: int
    web_name: str
    team: str
    position: str
    price: float
    predicted_points: float
    expected_minutes: float
    points_per_90: float
    n_fixtures: int
    status: str
    # Null until the gameweek is scored.
    actual_points: int | None
    actual_minutes: int | None


class PredictionPage(BaseModel):
    season: str
    gameweek: int
    model_version: str
    snapshot_id: str
    generated_at_utc: datetime
    deadline_utc: datetime
    items: list[Prediction]
    # Pass as `after` for the next page; null on the last one.
    next_cursor: str | None


class Club(BaseModel):
    team: str
    first_gameweek: int
    # None while the spell is open-ended.
    last_gameweek: int | None


class PlayedFixture(BaseModel):
    gameweek: int | None
    fixture_id: int
    opponent: str
    was_home: bool
    minutes: int
    total_points: int
    goals_scored: int
    assists: int
    clean_sheets: int
    bonus: int
    expected_goals: float | None
    expected_assists: float | None


class PlayerPrediction(BaseModel):
    gameweek: int
    model_version: str
    predicted_points: float
    expected_minutes: float
    actual_points: int | None
    actual_minutes: int | None


class Player(BaseModel):
    season: str
    player_id: int
    web_name: str
    first_name: str
    second_name: str
    position: str
    clubs: list[Club]
    history: list[PlayedFixture]
    predictions: list[PlayerPrediction]


class Model(BaseModel):
    model_version: str
    first_gameweek: int
    gameweeks_logged: list[int]
    gameweeks_scored: list[int]
    # Settled after the model's first entry, with no entry from it.
    gameweeks_missed: list[int]


class ModelList(BaseModel):
    season: str
    models: list[Model]


class GameweekMetrics(Metrics):
    gameweek: int


class Pooled(BaseModel):
    """Across every scored player-gameweek row, not a mean of weekly means."""

    all: Metrics | None
    predicted_to_play: Metrics | None
    minutes_mae: float | None


class ScorecardModel(Model):
    per_gameweek: list[GameweekMetrics]
    pooled: Pooled


class Scorecard(BaseModel):
    season: str
    harness_versions: list[str]
    models: list[ScorecardModel]


class Health(BaseModel):
    status: str


class Readiness(BaseModel):
    status: str
    database: str
    migration: str


# --- The optimiser (#83) -----------------------------------------------------


class OwnedPlayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player_id: Annotated[int, Field(ge=1, le=100_000, examples=[411])]
    # What FPL pays for him: purchase price plus half of any rise, rounded
    # down. On the transfers page, or GET /v1/entries/{id}/advice estimates it.
    selling_price: Annotated[float, Field(gt=0, le=20, examples=[7.6], description="£m")]


class OptimiseRequest(BaseModel):
    """A squad to plan transfers from, or none for the best squad from scratch."""

    model_config = ConfigDict(extra="forbid")

    season: Annotated[str | None, Field(pattern=r"^\d{4}/\d{2}$", examples=["2026/27"])] = None
    gameweek: Annotated[int | None, Field(
        ge=1, le=38, description="Defaults to the model's latest entry.")] = None
    model: Annotated[str, Field(pattern=r"^[A-Za-z0-9.-]{1,40}$",
                                examples=["baseline-v1"])] = "baseline-v1"
    squad: Annotated[list[OwnedPlayer] | None, Field(
        min_length=15, max_length=15,
        description="The fifteen owned. Without it, the best squad from scratch.")] = None
    bank: Annotated[float, Field(ge=0, le=100, description="£m; with a squad")] = 0.0
    free_transfers: Annotated[int, Field(ge=0, le=5)] = 1
    budget: Annotated[float, Field(
        ge=50, le=200, description="£m; without a squad")] = 100.0
    include_unavailable: bool = False
    horizon: Annotated[int, Field(
        ge=1, le=1, description="Gameweeks to plan over. One, until #55.")] = 1


class Pick(BaseModel):
    player_id: int
    web_name: str
    team: str
    position: str
    price: float
    predicted_points: float
    selling_price: float | None


class Squad(BaseModel):
    xi: list[Pick]
    bench: list[Pick]
    captain: Pick
    vice_captain: Pick
    formation: str
    cost: float
    budget: float
    # The XI's predicted points, the captain's counted twice.
    expected_points: float
    bench_points: float


class Transfer(BaseModel):
    out: Pick
    selling_price: float
    into: Pick


class Plan(BaseModel):
    transfers: list[Transfer]
    free_transfers: int
    hits: int
    hit_points: int
    bank: float
    bank_after: float
    # Over keeping the squad, net of hits: in the XI, captain included; and as
    # the optimiser counts it, with the bench at bench_weight.
    xi_gain: float
    gain: float
    bench_weight: float
    over_cap: dict[str, int]


class OptimiseResult(BaseModel):
    season: str
    gameweek: int
    model_version: str
    deadline_utc: datetime
    # The plan is for a deadline already gone: useful to look at, too late to act on.
    deadline_passed: bool
    cached: bool
    squad: Squad
    # Null for a squad from scratch.
    plan: Plan | None


class Estimates(BaseModel):
    """What the public FPL API does not give, and how it was filled in."""

    selling_prices: str
    free_transfers: str


class Advice(OptimiseResult):
    fpl_team_id: int
    # The gameweek whose picks are the squad planned from.
    picks_gameweek: int
    estimates: Estimates
