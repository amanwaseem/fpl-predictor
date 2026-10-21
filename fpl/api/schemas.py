"""What each route returns. These models are the API's contract, and its OpenAPI.

Numbers the database holds as exact decimals are served as JSON numbers: the
log writes at most two decimal places, which a float carries exactly enough.
"""

from datetime import datetime

from pydantic import BaseModel


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
