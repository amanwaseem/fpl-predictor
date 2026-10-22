"""The routes: /v1 for the data, /healthz and /readyz for whoever runs the service."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import Path as PathParam
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from fpl.api import queries, schemas
from fpl.api.problems import responses
from fpl.db import migrate

# Patterns as well as types: a value Postgres cannot hold, like a NUL byte,
# is refused here as a 422 rather than reaching the database as a 500.
# Examples are real values, so a client generated from the OpenAPI, or a
# fuzzer reading it, reaches data rather than only 404s.
SeasonParam = Annotated[str | None, Query(
    pattern=r"^\d{4}/\d{2}$", examples=["2026/27"],
    description="Defaults to the latest season.")]
GameweekParam = Annotated[int, PathParam(ge=1, le=38, examples=[6])]
ModelParam = Annotated[str, Query(pattern=r"^[A-Za-z0-9.-]{1,40}$",
                                  examples=["baseline-v1", "form-fixture-v1"],
                                  description="A model_version.")]

def database(request: Request) -> Engine:
    """The engine, not a connection: each route connects only once its parameters
    are valid, so a bad request is refused without touching the database."""
    return request.app.state.engine


Database = Annotated[Engine, Depends(database)]

v1 = APIRouter(responses=responses(404, 422))
health = APIRouter(tags=["health"])


@v1.get("/gameweeks", response_model=schemas.GameweekList, tags=["gameweeks"])
def list_gameweeks(db: Database, season: SeasonParam = None):
    """Every gameweek of the season, with the models that logged an entry for it."""
    with db.connect() as conn:
        season = queries.resolve_season(conn, season)
        return {"season": season, "gameweeks": queries.gameweeks(conn, season)}


@v1.get("/gameweeks/{gameweek}", response_model=schemas.GameweekDetail, tags=["gameweeks"])
def get_gameweek(db: Database, gameweek: GameweekParam, season: SeasonParam = None):
    """One gameweek: its fixtures, and each model's entry with its score once settled."""
    with db.connect() as conn:
        return queries.gameweek(conn, queries.resolve_season(conn, season), gameweek)


@v1.get("/gameweeks/{gameweek}/predictions", response_model=schemas.PredictionPage,
        tags=["gameweeks"])
def list_predictions(
    db: Database,
    gameweek: GameweekParam,
    model: ModelParam,
    season: SeasonParam = None,
    position: Literal["GKP", "DEF", "MID", "FWD"] | None = None,
    team: Annotated[str | None, Query(pattern=r"^[A-Z]{3}$", examples=["ARS"])] = None,
    max_price: Annotated[float | None, Query(ge=0, le=20, description="£m")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    after: Annotated[str | None, Query(
        pattern=r"^-?\d{1,20}(\.\d{1,30})?:\d{1,9}$", examples=["6.94:124"],
        description="next_cursor from the previous page: its last row's "
                    "predicted_points:player_id")] = None,
):
    """One committed entry's predictions, highest first, with actual points once scored."""
    with db.connect() as conn:
        season = queries.resolve_season(conn, season)
        return queries.predictions(conn, season, gameweek, model, position=position,
                                   team=team, max_price=max_price, limit=limit, after=after)


@v1.get("/players/{player_id}", response_model=schemas.Player, tags=["players"])
def get_player(db: Database,
               player_id: Annotated[int, PathParam(ge=1, le=100_000, examples=[411])],
               season: SeasonParam = None):
    """A player's clubs, matches and every prediction made for them, with actuals."""
    with db.connect() as conn:
        return queries.player(conn, queries.resolve_season(conn, season), player_id)


@v1.get("/models", response_model=schemas.ModelList, tags=["models"])
def list_models(db: Database, season: SeasonParam = None):
    """Every logged model: the gameweeks it logged, had scored, and missed."""
    with db.connect() as conn:
        season = queries.resolve_season(conn, season)
        return {"season": season, "models": queries.models(conn, season)}


@v1.get("/scorecard", response_model=schemas.Scorecard, tags=["models"])
def get_scorecard(db: Database, season: SeasonParam = None):
    """Each model's metrics per gameweek and pooled, with missed gameweeks shown."""
    with db.connect() as conn:
        return queries.scorecard(conn, queries.resolve_season(conn, season))


@health.get("/healthz", response_model=schemas.Health)
def healthz():
    """The process is up. Says nothing about the database."""
    return {"status": "ok"}


@health.get("/readyz", response_model=schemas.Readiness, responses=responses(503))
def readyz(db: Database):
    """The database answers and its schema is at the latest migration."""
    head = migrate.head()
    try:
        with db.connect() as conn:
            conn.execute(text("SELECT 1"))
            current = migrate.current(conn)
    except SQLAlchemyError:
        raise HTTPException(503, "The database is not reachable.") from None
    if current != head:
        raise HTTPException(503, f"The database is at migration {current}, not {head}.")
    return {"status": "ok", "database": "ok", "migration": current}
