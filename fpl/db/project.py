"""Load the committed log, predictions/ and scores/, into Postgres.

    fpl-project

The database is a projection of the log, not a second record of it. Every run
rebuilds the log tables from the files in one transaction, so the database
says what git says and nothing else; `fpl-reconcile` checks that it does.
Entries are read with fpl.log.read_entry, the same reader the verifier and the
scoring harness use.

Reads predictions/ and scores/; writes neither. Run from the repository root,
with DATABASE_URL set.
"""

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from fpl.db.models import PlayerScore, Prediction, PredictionEntry, ScoreEntry, Season
from fpl.db.session import make_engine
from fpl.log import LOG_DIR_NAME, read_entry
from fpl.paths import require_project_root
from fpl.score import SCORE_FIELDS, find_entries

SCORES_DIR = "scores"
# Any fixed key will do: it only has to be the same for every fpl-project.
PROJECT_LOCK = 80

PROVENANCE = ["model_version", "snapshot_id", "generated_at_utc", "deadline_utc"]
DECIMAL_FIELDS = ["price", "predicted_points", "expected_minutes", "points_per_90"]


@dataclass
class Log:
    """The committed log, as rows for each of the four log tables."""

    entries: list[dict]
    predictions: list[dict]
    score_entries: list[dict]
    player_scores: list[dict]

    def __str__(self):
        return (f"{len(self.entries)} entries, {len(self.predictions)} predictions, "
                f"{len(self.score_entries)} scored entries, "
                f"{len(self.player_scores)} player scores")


def season_for(deadline):
    """'2026/27' for any deadline from July 2026 to June 2027."""
    start = deadline.year if deadline.month >= 7 else deadline.year - 1
    return f"{start}/{(start + 1) % 100:02d}"


def timestamp(value):
    return datetime.fromisoformat(value)


def as_decimal(value):
    """A number read_entry parsed as a float, back as the decimal it was written as.

    repr is the shortest text that reads back as the same float, which for the
    log's few decimal places is the text in the file.
    """
    return Decimal(repr(value))


def read_entries(log_dir):
    """(entries, predictions) from every file in the log. Unreadable files are fatal."""
    entries, predictions = [], []
    for (gameweek, model_version), path in find_entries(log_dir).items():
        rows, faults = read_entry(path)
        if faults:
            raise SystemExit(f"{path} cannot be projected:\n  " + "\n  ".join(faults[:10]))
        provenance = {field: rows[0][field] for field in PROVENANCE}
        for row in rows:
            if row["gameweek"] != gameweek or any(row[f] != provenance[f] for f in PROVENANCE):
                raise SystemExit(
                    f"{path}: player {row['player_id']} disagrees with the filename or with "
                    "the first row about its gameweek or provenance.")
        if provenance["model_version"] != model_version:
            raise SystemExit(f"{path}: rows say model_version "
                             f"{provenance['model_version']!r}, the filename {model_version!r}")

        deadline = timestamp(provenance["deadline_utc"])
        key = {"season": season_for(deadline), "gameweek": gameweek,
               "model_version": model_version}
        entries.append(key | {"snapshot_id": provenance["snapshot_id"],
                              "generated_at_utc": timestamp(provenance["generated_at_utc"]),
                              "deadline_utc": deadline})
        for row in rows:
            predictions.append(key | {
                "player_id": row["player_id"], "web_name": row["web_name"],
                "team": row["team"], "position": row["position"],
                "n_fixtures": row["n_fixtures"], "status": row["status"],
            } | {field: as_decimal(row[field]) for field in DECIMAL_FIELDS})
    return entries, predictions


def read_scores(scores_dir, entries):
    """(score_entries, player_scores) from scores/, for entries already read.

    A player score keeps the file's `error` column, which the database computes
    for itself: fpl-reconcile compares the two, and fpl-project drops it.
    """
    season_of = {(e["gameweek"], e["model_version"]): e["season"] for e in entries}
    score_entries, player_scores = [], []
    for summary_path in sorted(Path(scores_dir).glob("gw*_*.json")):
        summary = json.loads(summary_path.read_text())
        entry = (summary["gameweek"], summary["model_version"])
        if entry not in season_of:
            raise SystemExit(f"{summary_path} scores an entry that is not in the log: "
                             f"GW{entry[0]} {entry[1]}")
        key = {"season": season_of[entry], "gameweek": entry[0], "model_version": entry[1]}
        headline = summary["metrics"]["all"]
        score_entries.append(key | {
            "harness_version": summary["harness_version"],
            "actuals_snapshot_id": summary["actuals_snapshot_id"],
            "n": headline["n"], "mae": headline["mae"], "rmse": headline["rmse"],
            "bias": headline["bias"], "spearman": headline.get("spearman"),
            "summary": summary,
        })

        with summary_path.with_suffix(".csv").open(newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != SCORE_FIELDS:
                raise SystemExit(f"{summary_path.with_suffix('.csv')} has columns "
                                 f"{reader.fieldnames}, expected {SCORE_FIELDS}")
            for row in reader:
                player_scores.append(key | {
                    "player_id": int(row["player_id"]),
                    "predicted_points": Decimal(row["predicted_points"]),
                    "actual_points": int(row["actual_points"]),
                    "expected_minutes": Decimal(row["expected_minutes"]),
                    "actual_minutes": int(row["actual_minutes"]),
                    "error": Decimal(row["error"]),
                })
    return score_entries, player_scores


def read_log(log_dir=LOG_DIR_NAME, scores_dir=SCORES_DIR):
    entries, predictions = read_entries(log_dir)
    return Log(entries, predictions, *read_scores(scores_dir, entries))


def project(engine, log):
    """Replace the log tables' contents with `log`, in one transaction."""
    with engine.begin() as connection:
        connection.execute(select(func.pg_advisory_xact_lock(PROJECT_LOCK)))
        # Cascades to predictions, score_entries and player_scores.
        connection.execute(delete(PredictionEntry))
        for season in sorted({e["season"] for e in log.entries}):
            connection.execute(pg_insert(Season).values(season=season).on_conflict_do_nothing())
        for model, rows in ((PredictionEntry, log.entries), (Prediction, log.predictions),
                            (ScoreEntry, log.score_entries),
                            (PlayerScore, [{k: v for k, v in r.items() if k != "error"}
                                           for r in log.player_scores])):
            if rows:
                connection.execute(insert(model), rows)


def main():
    log = read_log()
    engine = make_engine()
    try:
        project(engine, log)
    finally:
        engine.dispose()
    print(f"Projected the log into the database: {log}.")
    return 0


def cli(argv=None):
    """`fpl-project`, or `python -m fpl.db.project`."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.parse_args(argv)
    require_project_root()
    return main()


if __name__ == "__main__":
    sys.exit(cli())
