"""gbm-v1: gradient-boosted trees on the feature table (#85, ADR 0019).

    fpl-predict-gbm                  # predict the next gameweek, into predictions/
    fpl-predict-gbm --out scratch/   # exploratory, never the log

Four LightGBM models, trained on 2016/17-2025/26 by fpl.models.train_gbm and
evaluated here in plain Python (fpl.models.trees), so a prediction still needs
`requests` alone:

- **Minutes, in two stages.** P(60+): the chance of playing an hour, then
  minutes given 60+ and minutes given fewer. Expected minutes is
  p * long + (1 - p) * short, at most 90 a fixture.
- **Points**: a Tweedie regression, whose mass at zero and long right tail
  are the shape of a gameweek's points.

Each row of inputs is fpl.models.feature_table's, built here from the snapshot
just as fpl-features builds it from the database for training. Past seasons
carry no injury flags, so the models predict from form alone, which already
includes the ordinary chance of missing a game. The snapshot's flag adds what
it knows about this player this week: its chance of playing scales points and
minutes, as it does for every logged model and as fpl-verify checks. A player
flagged available is unchanged, and one who cannot play is zeroed.

The trained models are one JSON file, a GitHub release asset named in
models/registry.json with its SHA-256; it is fetched once into data/models/
and refused if its hash differs. A model is fixed from its first entry
(ADR 0005): retraining is a new version.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import requests

from fpl.ingest.snapshot import (
    availability,
    fixture_counts,
    load_snapshot,
    player_summaries,
    resolve_target_gw,
    usable_rounds,
)
from fpl.log.contract import write_entry
from fpl.models.feature_table import FEATURE_VERSION, SeasonData, build_gameweek, season_after
from fpl.models.features import previous_season
from fpl.models.trees import Model
from fpl.paths import require_project_root

MODEL_VERSION = "gbm-v1"
REGISTRY = Path("models/registry.json")
CACHE = Path("data/models")
TIMEOUT = 60
POSITIONS = ("GKP", "DEF", "MID", "FWD")
FULL_MATCH = 90.0

# The inputs, in the order the models were trained on. Position is four
# indicators rather than a category, which fpl.models.trees does not read.
FEATURES = [
    "gameweek",
    "n_fixtures",
    "n_home",
    "price",
    "minutes_last1",
    "minutes_mean3",
    "minutes_mean5",
    "appearances_last5",
    "sixty_last5",
    "season_rounds",
    "season_minutes",
    "season_points",
    "points_per90_3",
    "points_per90_5",
    "points_per90_season",
    "xg_per90_5",
    "xa_per90_5",
    "xgi_per90_season",
    "bonus_per90_season",
    "last_season_minutes",
    "last_season_points_per90",
    "team_attack",
    "team_defence",
    "opponent_attack",
    "opponent_defence",
    "expected_goals_for",
    "expected_goals_against",
    "expected_clean_sheets",
] + [f"is_{p.lower()}" for p in POSITIONS]
PARTS = ("sixty", "minutes_long", "minutes_short", "points")


def vector(row):
    """A feature-table row as the models' input list. None is missing."""
    values = dict(row)
    for position in POSITIONS:
        values[f"is_{position.lower()}"] = 1.0 if row["position"] == position else 0.0
    return [None if values.get(name) is None else float(values[name]) for name in FEATURES]


class Bundle:
    """The four trained models, and what they were trained on."""

    def __init__(self, artifact):
        if artifact["features"] != FEATURES:
            raise ValueError("the artifact was trained on different inputs than this code builds")
        # The same columns can change what they mean; the version says when.
        if artifact.get("feature_version") != FEATURE_VERSION:
            raise ValueError(
                f"the artifact was trained on {artifact.get('feature_version')} features, and "
                f"this code builds {FEATURE_VERSION}: a new feature version is a new model"
            )
        self.meta = {k: v for k, v in artifact.items() if k != "models"}
        # Set by load(), from the registry: the artifact's identity.
        self.sha256 = None
        self.models = {part: Model(artifact["models"][part]) for part in PARTS}
        for part, model in self.models.items():
            if model.feature_names != FEATURES:
                raise ValueError(f"the {part} model was trained on other inputs")

    def predict(self, row):
        """(points, minutes) for one feature-table row, before the snapshot's availability."""
        x = vector(row)
        p = self.models["sixty"].predict(x)
        long, short = (self.models[k].predict(x) for k in ("minutes_long", "minutes_short"))
        cap = FULL_MATCH * row["n_fixtures"]
        minutes = min(max(p * long + (1 - p) * short, 0.0), cap)
        return max(self.models["points"].predict(x), 0.0), minutes


def registered(version=MODEL_VERSION, registry=REGISTRY):
    entries = json.loads(Path(registry).read_text())
    if version not in entries:
        raise SystemExit(f"{version} is not in {registry}.")
    return entries[version]


def load(version=MODEL_VERSION, registry=REGISTRY, cache=CACHE):
    """The registered artifact, from the cache or its release, checked against its hash."""
    entry = registered(version, registry)
    path = Path(cache) / f"{entry['sha256']}.json"
    if path.exists():
        body = path.read_bytes()
        where = str(path)
    else:
        response = requests.get(entry["url"], timeout=TIMEOUT)
        response.raise_for_status()
        body, where = response.content, entry["url"]
    found = hashlib.sha256(body).hexdigest()
    if found != entry["sha256"]:
        raise SystemExit(
            f"{where} has SHA-256 {found}, not the registered {entry['sha256']}. "
            "Nothing was predicted"
            + (
                f"; delete {path} and run again."
                if where == str(path)
                else ", and nothing was cached."
            )
        )
    if not path.exists():
        # Cached under its hash only once it is known to be the registered file.
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        partial.write_bytes(body)
        partial.replace(path)
    bundle = Bundle(json.loads(body))
    bundle.sha256 = found
    return bundle


def season_data(bootstrap, fixtures, histories, pasts, usable):
    """A SeasonData from a snapshot (or the backtest's view of one).

    A player's club for the gameweek is the snapshot's, and his last season is
    his own history_past row for it: the same values fpl-features takes from
    the database, read from where a snapshot keeps them.
    """
    # Named as the logged models name it: from GW1's deadline.
    before = previous_season(bootstrap["events"])
    season = season_after(before)
    positions = {t["id"]: t["singular_name_short"] for t in bootstrap["element_types"]}
    players, clubs, previous = {}, {}, {}
    for e in bootstrap["elements"]:
        if positions.get(e["element_type"]) not in POSITIONS:
            continue
        past = next(
            (p for p in (pasts or {}).get(e["id"], []) if p.get("season_name") == before), None
        )
        # A code to join on: FPL's, from history_past, else one no other player has.
        code = past.get("element_code") if past else None
        code = code if code is not None else -e["id"]
        players[e["id"]] = {"position": positions[e["element_type"]], "code": code}
        clubs[e["id"]] = [(e["team"], 1, 38)]
        if past:
            previous[code] = {"minutes": past["minutes"], "points": past["total_points"]}
    return SeasonData(
        season=season,
        fixtures=fixtures,
        histories=histories,
        settled=set(usable),
        clubs=clubs,
        players=players,
        previous=previous,
    )


def predict_rows(bootstrap, fixtures, histories, pasts, target_gw, usable, bundle):
    """Predictions for every player with a history, as log rows without provenance."""
    data = season_data(bootstrap, fixtures, histories, pasts, usable)
    features = {r["element_id"]: r for r in build_gameweek(data, target_gw)}
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    positions = {p["id"]: p["singular_name_short"] for p in bootstrap["element_types"]}
    counts = fixture_counts(fixtures, target_gw)
    rows = []
    for player in bootstrap["elements"]:
        pid = player["id"]
        if pid not in histories:
            continue
        points, minutes = bundle.predict(features[pid]) if pid in features else (0.0, 0.0)
        chance = availability(player)
        points, minutes = round(points * chance, 2), round(minutes * chance, 1)
        rows.append(
            {
                "gameweek": target_gw,
                "player_id": pid,
                "web_name": player["web_name"],
                "team": teams.get(player["team"], "UNK"),
                "position": positions.get(player["element_type"], "UNK"),
                "price": player["now_cost"] / 10.0,
                "predicted_points": points,
                "expected_minutes": minutes,
                "points_per_90": round(points / minutes * FULL_MATCH, 2) if minutes > 0 else 0.0,
                "n_fixtures": counts.get(player["team"], 0),
                "status": player.get("status", ""),
            }
        )
    rows.sort(key=lambda r: (-r["predicted_points"], r["player_id"]))
    return rows


def main(requested_gw, out_dir, snapshot_id=None):
    snap, bootstrap, fixtures, players_dir = load_snapshot(snapshot_id)
    target_gw, deadline = resolve_target_gw(bootstrap["events"], requested_gw)
    usable, excluded = usable_rounds(bootstrap["events"], target_gw)
    print(f"snapshot:  {snap.name}")
    print(f"target:    GW{target_gw}")
    print(f"deadline:  {deadline}")
    print(f"included: {', '.join(str(r) for r in sorted(usable)) or 'none'}")
    print(f"excluded: {', '.join(str(r) for r in excluded) or 'none'}")
    if not usable:
        raise SystemExit(f"\nNo settled rounds before GW{target_gw}. Nothing was written.")
    # After the refusals above, so a run that will not predict fetches nothing.
    bundle = load()
    print(f"model:     {MODEL_VERSION}, artifact {(bundle.sha256 or 'unregistered')[:12]}")
    histories, pasts = player_summaries(bootstrap, players_dir)
    rows = predict_rows(bootstrap, fixtures, histories, pasts, target_gw, usable, bundle)
    outpath = write_entry(rows, out_dir, target_gw, MODEL_VERSION, snap.name, deadline)
    print(f"\nwrote {len(rows)} predictions -> {outpath}\n")
    for r in rows[:20]:
        print(
            f"{r['web_name'][:17]:<18}{r['team']:<6}{r['position']:<5}"
            f"{r['predicted_points']:>6}{r['expected_minutes']:>7}{r['points_per_90']:>7}"
        )
    return 0


def cli(argv=None):
    """`fpl-predict-gbm`, or `python -m fpl.models.gbm`."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gw", type=int, default=None, help="target gameweek (default: next)")
    ap.add_argument(
        "--out",
        default="predictions",
        metavar="DIR",
        help="output directory (default: predictions/, the append-only log)",
    )
    ap.add_argument(
        "--snapshot",
        default=None,
        metavar="ID",
        help="snapshot id under data/raw/ (default: LATEST)",
    )
    args = ap.parse_args(argv)
    require_project_root()
    return main(args.gw, args.out, args.snapshot)


if __name__ == "__main__":
    sys.exit(cli())
