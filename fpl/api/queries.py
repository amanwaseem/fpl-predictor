"""The SQL behind each route, as plain dicts shaped like fpl.api.schemas.

Pooled metrics come from fpl.metrics and missed gameweeks follow
fpl.score.cumulative, so the API's scorecard cannot disagree with the
scorecard scores/ writes.
"""

from collections import defaultdict
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

from fpl import metrics
from fpl.db.models import (
    Fixture,
    Gameweek,
    Player,
    PlayerClub,
    PlayerFixtureStats,
    PlayerScore,
    Prediction,
    PredictionEntry,
    ScoreEntry,
    Season,
    SeasonPlayer,
    Team,
)


def not_found(detail):
    return HTTPException(404, detail)


def resolve_season(connection, season):
    """The season asked for, or the latest one in the database."""
    if season is None:
        season = connection.scalar(select(func.max(Season.season)))
        if season is None:
            raise not_found("The database holds no season yet.")
    elif connection.scalar(select(Season.season).where(Season.season == season)) is None:
        raise not_found(f"No season {season} in the database.")
    return season


def _logged(connection, season):
    """{gameweek: [model_version, ...]} for every committed entry this season."""
    logged = defaultdict(list)
    for gameweek, model in connection.execute(
            select(PredictionEntry.gameweek, PredictionEntry.model_version)
            .where(PredictionEntry.season == season)
            .order_by(PredictionEntry.gameweek, PredictionEntry.model_version)):
        logged[gameweek].append(model)
    return logged


def gameweeks(connection, season):
    logged = _logged(connection, season)
    rows = connection.execute(select(Gameweek).where(Gameweek.season == season)
                              .order_by(Gameweek.gameweek)).mappings()
    return [dict(row) | {"models_logged": logged.get(row["gameweek"], [])} for row in rows]


def gameweek(connection, season, number):
    row = connection.execute(select(Gameweek).where(
        Gameweek.season == season, Gameweek.gameweek == number)).mappings().first()
    if row is None:
        raise not_found(f"No gameweek {number} in {season}.")

    home, away = aliased(Team), aliased(Team)
    fixtures = connection.execute(
        select(Fixture.fixture_id, Fixture.kickoff_utc, home.short_name.label("home"),
               away.short_name.label("away"), Fixture.team_h_score.label("home_score"),
               Fixture.team_a_score.label("away_score"), Fixture.finished)
        .join(home, and_(home.season == Fixture.season, home.team_id == Fixture.team_h))
        .join(away, and_(away.season == Fixture.season, away.team_id == Fixture.team_a))
        .where(Fixture.season == season, Fixture.gameweek == number)
        .order_by(Fixture.kickoff_utc, Fixture.fixture_id)).mappings()

    entries = []
    for entry in connection.execute(
            select(PredictionEntry, ScoreEntry.n, ScoreEntry.mae, ScoreEntry.rmse,
                   ScoreEntry.bias, ScoreEntry.spearman)
            .outerjoin(ScoreEntry, and_(
                ScoreEntry.season == PredictionEntry.season,
                ScoreEntry.gameweek == PredictionEntry.gameweek,
                ScoreEntry.model_version == PredictionEntry.model_version))
            .where(PredictionEntry.season == season, PredictionEntry.gameweek == number)
            .order_by(PredictionEntry.model_version)).mappings():
        score = ({k: entry[k] for k in ("n", "mae", "rmse", "bias", "spearman")}
                 if entry["n"] is not None else None)
        entries.append({k: entry[k] for k in ("model_version", "snapshot_id",
                                              "generated_at_utc", "deadline_utc")}
                       | {"score": score})

    return dict(row) | {"season": season, "fixtures": [dict(f) for f in fixtures],
                        "entries": entries,
                        "models_logged": [e["model_version"] for e in entries]}


def encode_cursor(predicted_points, player_id):
    """The last row's sort key, as text: '6.94:124'.

    Not opaque on purpose. routes.py's pattern for `after` then describes every
    valid cursor exactly, so no string the schema allows can be refused.
    """
    return f"{predicted_points}:{player_id}"


def decode_cursor(cursor):
    points, player_id = cursor.split(":")
    return Decimal(points), int(player_id)


def predictions(connection, season, number, model, *, position=None, team=None,
                max_price=None, limit=50, after=None):
    """One entry's rows, in the log's order: predicted points down, then player id.

    Keyset pagination: `after` is the last row's (predicted_points, player_id),
    so a page is the same however many rows came before it.
    """
    entry = connection.execute(select(PredictionEntry).where(
        PredictionEntry.season == season, PredictionEntry.gameweek == number,
        PredictionEntry.model_version == model)).mappings().first()
    if entry is None:
        raise not_found(f"No {model} entry for gameweek {number} of {season}.")

    query = (
        select(Prediction, PlayerScore.actual_points, PlayerScore.actual_minutes)
        .outerjoin(PlayerScore, and_(
            PlayerScore.season == Prediction.season,
            PlayerScore.gameweek == Prediction.gameweek,
            PlayerScore.model_version == Prediction.model_version,
            PlayerScore.player_id == Prediction.player_id))
        .where(Prediction.season == season, Prediction.gameweek == number,
               Prediction.model_version == model)
        .order_by(Prediction.predicted_points.desc(), Prediction.player_id)
        .limit(limit + 1))
    if position is not None:
        query = query.where(Prediction.position == position)
    if team is not None:
        query = query.where(Prediction.team == team)
    if max_price is not None:
        query = query.where(Prediction.price <= max_price)
    if after is not None:
        points, player_id = decode_cursor(after)
        query = query.where(or_(
            Prediction.predicted_points < points,
            and_(Prediction.predicted_points == points, Prediction.player_id > player_id)))

    rows = [dict(r) for r in connection.execute(query).mappings()]
    page, more = rows[:limit], len(rows) > limit
    next_cursor = (encode_cursor(page[-1]["predicted_points"], page[-1]["player_id"])
                   if more else None)
    return dict(entry) | {"items": page, "next_cursor": next_cursor}


def player(connection, season, player_id):
    row = connection.execute(
        select(SeasonPlayer.element_id.label("player_id"), SeasonPlayer.web_name,
               SeasonPlayer.position, Player.first_name, Player.second_name)
        .join(Player, Player.code == SeasonPlayer.player_code)
        .where(SeasonPlayer.season == season, SeasonPlayer.element_id == player_id)
    ).mappings().first()
    if row is None:
        raise not_found(f"No player {player_id} in {season}.")

    clubs = [
        {"team": team, "first_gameweek": gameweeks.lower, "last_gameweek": gameweeks.upper - 1}
        for team, gameweeks in connection.execute(
            select(Team.short_name, PlayerClub.gameweeks)
            .join(Team, and_(Team.season == PlayerClub.season,
                             Team.team_id == PlayerClub.team_id))
            .where(PlayerClub.season == season, PlayerClub.element_id == player_id)
            .order_by(PlayerClub.gameweeks))]

    opponent = aliased(Team)
    history = connection.execute(
        select(Fixture.gameweek, PlayerFixtureStats.fixture_id,
               opponent.short_name.label("opponent"), PlayerFixtureStats.was_home,
               PlayerFixtureStats.minutes, PlayerFixtureStats.total_points,
               PlayerFixtureStats.goals_scored, PlayerFixtureStats.assists,
               PlayerFixtureStats.clean_sheets, PlayerFixtureStats.bonus,
               PlayerFixtureStats.expected_goals, PlayerFixtureStats.expected_assists)
        .join(Fixture, and_(Fixture.season == PlayerFixtureStats.season,
                            Fixture.fixture_id == PlayerFixtureStats.fixture_id))
        .join(opponent, and_(opponent.season == PlayerFixtureStats.season,
                             opponent.team_id == PlayerFixtureStats.opponent_team))
        .where(PlayerFixtureStats.season == season, PlayerFixtureStats.element_id == player_id)
        .order_by(Fixture.kickoff_utc, Fixture.fixture_id)).mappings()

    predicted = connection.execute(
        select(Prediction.gameweek, Prediction.model_version, Prediction.predicted_points,
               Prediction.expected_minutes, PlayerScore.actual_points,
               PlayerScore.actual_minutes)
        .outerjoin(PlayerScore, and_(
            PlayerScore.season == Prediction.season,
            PlayerScore.gameweek == Prediction.gameweek,
            PlayerScore.model_version == Prediction.model_version,
            PlayerScore.player_id == Prediction.player_id))
        .where(Prediction.season == season, Prediction.player_id == player_id)
        .order_by(Prediction.gameweek, Prediction.model_version)).mappings()

    return dict(row) | {"season": season, "clubs": clubs,
                        "history": [dict(h) for h in history],
                        "predictions": [dict(p) for p in predicted]}


def models(connection, season):
    """Every model logged this season: logged, scored and missed gameweeks.

    Missed follows fpl.score.cumulative: settled after the model's first entry,
    with no entry from it.
    """
    logged = defaultdict(list)
    for gameweek, models_logged in _logged(connection, season).items():
        for model in models_logged:
            logged[model].append(gameweek)
    scored = defaultdict(list)
    for gameweek, model in connection.execute(
            select(ScoreEntry.gameweek, ScoreEntry.model_version)
            .where(ScoreEntry.season == season).order_by(ScoreEntry.gameweek)):
        scored[model].append(gameweek)
    settled = set(connection.scalars(select(Gameweek.gameweek).where(
        Gameweek.season == season, Gameweek.finished, Gameweek.data_checked)))
    last_settled = max(settled, default=0)

    return [{"model_version": model, "first_gameweek": gameweeks[0],
             "gameweeks_logged": gameweeks, "gameweeks_scored": scored[model],
             "gameweeks_missed": [gw for gw in range(gameweeks[0], last_settled + 1)
                                  if gw in settled and gw not in gameweeks]}
            for model, gameweeks in sorted(logged.items())]


def scorecard(connection, season):
    per_gameweek = defaultdict(list)
    for row in connection.execute(
            select(ScoreEntry.model_version, ScoreEntry.gameweek, ScoreEntry.n, ScoreEntry.mae,
                   ScoreEntry.rmse, ScoreEntry.bias, ScoreEntry.spearman)
            .where(ScoreEntry.season == season).order_by(ScoreEntry.gameweek)).mappings():
        per_gameweek[row["model_version"]].append(
            {k: v for k, v in row.items() if k != "model_version"})

    rows = defaultdict(lambda: defaultdict(list))
    for model, gameweek, predicted, actual, expected_minutes, actual_minutes in connection.execute(
            select(PlayerScore.model_version, PlayerScore.gameweek, PlayerScore.predicted_points,
                   PlayerScore.actual_points, PlayerScore.expected_minutes,
                   PlayerScore.actual_minutes).where(PlayerScore.season == season)):
        rows[model][gameweek].append(
            (float(predicted), actual, float(expected_minutes), actual_minutes))

    def pooled(model):
        groups = list(rows[model].values())
        if not groups:
            return {"all": None, "predicted_to_play": None, "minutes_mae": None}
        playing = [[(p, a) for p, a, m, _ in group if m > 0] for group in groups]
        return {
            "all": metrics.pooled_summary([[(p, a) for p, a, _, _ in g] for g in groups]),
            "predicted_to_play": metrics.pooled_summary(playing) if any(playing) else None,
            "minutes_mae": metrics.mae([(m, am) for g in groups for _, _, m, am in g]),
        }

    harness_versions = sorted(connection.scalars(
        select(ScoreEntry.harness_version).where(ScoreEntry.season == season).distinct()))
    return {"season": season, "harness_versions": harness_versions,
            "models": [model | {"per_gameweek": per_gameweek[model["model_version"]],
                                "pooled": pooled(model["model_version"])}
                       for model in models(connection, season)]}
