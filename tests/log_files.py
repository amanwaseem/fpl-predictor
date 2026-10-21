"""A small committed log on disk, predictions/ and scores/, for the projection tests."""

import csv
import json
from pathlib import Path

from fpl.score import SCORE_FIELDS
from tests.fixtures import ELEMENT_TYPES, element, entry_rows, fixture, team, write_csv

DEADLINE = "2026-10-23T17:30:00Z"
GENERATED = "2026-10-23T08:05:00Z"
SNAPSHOT = "20261023T080000Z"

BOOTSTRAP = {
    "teams": [team(1, "ARS"), team(2, "AVL")],
    "element_types": ELEMENT_TYPES,
    "elements": [element(1, team=1, now_cost=101), element(2, team=2, element_type=4),
                 element(3, team=1, element_type=1, status="i")],
}


def write_entry(root, model_version="baseline-v1", gameweek=8, *, rows=None):
    if rows is None:
        rows = entry_rows(BOOTSTRAP, [fixture(gameweek, 1, 2)], gameweek, DEADLINE,
                          snapshot_id=SNAPSHOT, model_version=model_version,
                          generated_at=GENERATED)
    return write_csv(Path(root) / "predictions" / f"gw{gameweek:02d}_{model_version}.csv", rows)


def write_score(root, model_version="baseline-v1", gameweek=8, *, actual=(6, 2, 0)):
    """scores/ for an entry written by write_entry: its JSON summary and CSV."""
    entry = Path(root) / "predictions" / f"gw{gameweek:02d}_{model_version}.csv"
    with entry.open(newline="") as f:
        predicted = list(csv.DictReader(f))

    stem = Path(root) / "scores" / f"gw{gameweek:02d}_{model_version}"
    stem.parent.mkdir(parents=True, exist_ok=True)
    with stem.with_suffix(".csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SCORE_FIELDS)
        writer.writeheader()
        for row, points in zip(predicted, actual, strict=True):
            writer.writerow({
                "player_id": row["player_id"], "web_name": row["web_name"],
                "team": row["team"], "position": row["position"],
                "predicted_points": row["predicted_points"], "actual_points": points,
                "error": round(float(row["predicted_points"]) - points, 2),
                "expected_minutes": row["expected_minutes"], "actual_minutes": 90,
            })
    stem.with_suffix(".json").write_text(json.dumps({
        "gameweek": gameweek, "model_version": model_version,
        "harness_version": "harness-v1", "actuals_snapshot_id": "20261027T080000Z",
        "prediction_snapshot_id": SNAPSHOT, "deadline_utc": DEADLINE,
        "metrics": {"all": {"bias": -0.5, "mae": 1.25, "n": len(actual),
                            "rmse": 1.5, "spearman": 0.5}},
    }))
    return stem
