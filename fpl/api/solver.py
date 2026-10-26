"""The optimiser behind /v1/optimise and the advice route (#83).

The predictions come from the committed log, as projected into the database:
the same rows fpl-optimise reads from predictions/, so the API and the CLI give
the same answer for the same squad. Results are cached in Postgres by a hash
of everything that decides them (fpl.db.models.OptimiseResult).
"""

import hashlib
import json
import os
import time
from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from fpl import optimise
from fpl.api import observability
from fpl.db.models import OptimiseResult, Prediction, PredictionEntry
from fpl.evaluation.score import BASELINE_MODEL

# Seconds for all of one request's solves together. A full squad from 600-odd
# players solves in well under a second; the limit is for the pathological
# request, so a slow one fails cleanly rather than holding a worker.
TIME_LIMIT = float(os.environ.get("FPL_SOLVER_TIME_LIMIT", "10"))
DEFAULT_MODEL = BASELINE_MODEL


def entry_for(connection, season, gameweek, model):
    """(gameweek, deadline_utc) of the entry asked for: the latest one when no gameweek is."""
    query = select(PredictionEntry.gameweek, PredictionEntry.deadline_utc).where(
        PredictionEntry.season == season, PredictionEntry.model_version == model
    )
    if gameweek is None:
        row = connection.execute(query.order_by(PredictionEntry.gameweek.desc()).limit(1)).first()
        if row is None:
            raise HTTPException(404, f"{model} has no entry in {season}.")
    else:
        row = connection.execute(query.where(PredictionEntry.gameweek == gameweek)).first()
        if row is None:
            raise HTTPException(404, f"{model} has no entry for GW{gameweek} in {season}.")
    return row.gameweek, row.deadline_utc


def predictions(connection, season, gameweek, model):
    """The entry's rows as fpl.optimise reads them: floats, as read_entry gives."""
    rows = connection.execute(
        select(
            Prediction.player_id,
            Prediction.web_name,
            Prediction.team,
            Prediction.position,
            Prediction.price,
            Prediction.predicted_points,
            Prediction.status,
        ).where(
            Prediction.season == season,
            Prediction.gameweek == gameweek,
            Prediction.model_version == model,
        )
    ).mappings()
    return [
        dict(r) | {"price": float(r["price"]), "predicted_points": float(r["predicted_points"])}
        for r in rows
    ]


def request_hash(**decided_by):
    """sha256 of the canonical JSON of everything that decides a result."""
    canonical = json.dumps(decided_by, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def cached(connection, key):
    return connection.scalar(
        select(OptimiseResult.result).where(OptimiseResult.request_hash == key)
    )


def store(engine, key, season, gameweek, model, result):
    """Keep a result. A cache that cannot be written is no reason to fail the request:
    the projection may have replaced the entry mid-solve, during a deploy."""
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(OptimiseResult)
                .values(
                    request_hash=key,
                    season=season,
                    gameweek=gameweek,
                    model_version=model,
                    result=result,
                )
                .on_conflict_do_nothing()
            )
    except IntegrityError:
        pass


def solve(
    engine,
    season,
    gameweek,
    model,
    *,
    squad=None,
    bank=None,
    free_transfers=None,
    budget=optimise.BUDGET,
    include_unavailable=False,
    entry=None,
    rows=None,
):
    """A result dict shaped like fpl.api.schemas.OptimiseResult.

    With `squad` ({player_id: selling_price}), the best transfers from it;
    without, the best squad from scratch within `budget`. A caller that has
    already read the entry passes `entry` (gameweek, deadline) and its `rows`.
    """
    with engine.connect() as connection:
        gameweek, deadline = entry or entry_for(connection, season, gameweek, model)
        key = request_hash(
            season=season,
            gameweek=gameweek,
            model=model,
            budget=budget,
            include_unavailable=include_unavailable,
            bank=bank,
            free_transfers=free_transfers,
            squad=sorted(squad.items()) if squad is not None else None,
        )
        result = cached(connection, key)
        if result is None and rows is None:
            rows = predictions(connection, season, gameweek, model)

    hit = result is not None
    if not hit:
        start = time.perf_counter()
        result = _solve(rows, squad, bank, free_transfers, budget, include_unavailable)
        observability.record_solver_time(time.perf_counter() - start)
        store(engine, key, season, gameweek, model, result)
    return {
        "season": season,
        "gameweek": gameweek,
        "model_version": model,
        "deadline_utc": deadline,
        "deadline_passed": datetime.now(UTC) >= deadline,
        "cached": hit,
    } | result


def _solve(rows, squad, bank, free_transfers, budget, include_unavailable):
    try:
        if squad is None:
            result = optimise.best_squad(
                rows, budget=budget, include_unavailable=include_unavailable, time_limit=TIME_LIMIT
            )
            return {"squad": asdict(result), "plan": None}
        plan = asdict(
            optimise.best_transfers(
                rows,
                squad,
                bank,
                free_transfers,
                include_unavailable=include_unavailable,
                time_limit=TIME_LIMIT,
            )
        )
        return {"squad": plan.pop("squad"), "plan": plan}
    except optimise.SolverTimeout:
        raise HTTPException(
            503,
            f"The solver did not finish within {TIME_LIMIT:g} seconds.",
            headers={"Retry-After": "60"},
        ) from None
    except optimise.SolverFailure as e:
        raise HTTPException(
            503, f"The solver failed: {e}.", headers={"Retry-After": "60"}
        ) from None
    except ValueError as e:
        # The squad or the pool cannot be solved: the request's fault, said why.
        raise HTTPException(422, str(e)) from None
