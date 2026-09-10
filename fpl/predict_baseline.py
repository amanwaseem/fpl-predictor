"""Rolling-form baseline predictor.

Deliberately simple: no training, no ML. Predicts a player's points for the
target gameweek from their recent minutes and scoring rate, adjusted for
availability and fixture count.

This is the number every future model has to beat. If a component model can't
outperform "what did they do recently", it isn't earning its complexity.

Writes an immutable prediction log entry to predictions/. That file gets
committed BEFORE the deadline and is never rewritten.

Usage:
    python -m fpl.predict_baseline            # predict the next gameweek
    python -m fpl.predict_baseline --gw 4     # predict a specific gameweek
    python -m fpl.predict_baseline --out scratch/  # exploratory, not the log

Run from the repository root: paths are resolved against the working
directory, not this file.
"""

import argparse
import json

from fpl.features import availability, recent_history, weighted
from fpl.log import write_entry
from fpl.snapshot import (
    fixture_counts,
    load_snapshot,
    resolve_target_gw,
    usable_rounds,
)

MODEL_VERSION = "baseline-v1"

# Shrinkage: pseudo-minutes of league-average scoring blended into every
# player's rate. Stops 12-minute cameos dominating the top of the table.
PRIOR_MINUTES = 270.0

# Rough points-per-90 for a regular starter, by position.
POSITION_PRIOR_PP90 = {"GKP": 3.2, "DEF": 3.3, "MID": 3.6, "FWD": 3.8}


def predict_player(player, history, position, n_fixtures, target_gw, usable):
    """Expected points for one player in the target gameweek."""
    if n_fixtures == 0:
        return 0.0, 0.0, 0.0  # blank gameweek

    recent = recent_history(history, target_gw, usable)
    if not recent:
        return 0.0, 0.0, 0.0  # no appearances to reason from

    # Minutes per fixture, not per round. A past double gameweek puts up to
    # 180 minutes in one round; averaging that raw and only then clipping to 90
    # drags the mean upward across the whole lookback window, inflating every
    # prediction for a player who happened to have a double recently.
    minutes_list = [r["minutes"] / r["fixtures"] for r in recent]
    avg_minutes, _ = weighted(minutes_list)
    exp_minutes = min(avg_minutes, 90.0) * availability(player)

    # Points per 90, shrunk toward the positional prior. A player with few
    # observed minutes sits close to the prior; a regular starter dominates it.
    obs_minutes = sum(r["minutes"] for r in recent)
    obs_points = sum(r["points"] for r in recent)
    prior_pp90 = POSITION_PRIOR_PP90.get(position, 3.5)
    prior_points = prior_pp90 * (PRIOR_MINUTES / 90.0)

    pp90 = (obs_points + prior_points) / ((obs_minutes + PRIOR_MINUTES) / 90.0)

    predicted = pp90 * (exp_minutes / 90.0) * n_fixtures
    return round(predicted, 2), round(exp_minutes, 1), round(pp90, 2)


def main(requested_gw, out_dir):
    snap, bootstrap, fixtures, players_dir = load_snapshot()
    target_gw, deadline = resolve_target_gw(bootstrap["events"], requested_gw)

    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    positions = {p["id"]: p["singular_name_short"] for p in bootstrap["element_types"]}
    counts = fixture_counts(fixtures, target_gw)
    usable, excluded = usable_rounds(bootstrap["events"], target_gw)

    print(f"snapshot:  {snap.name}")
    print(f"target:    GW{target_gw}")
    print(f"deadline:  {deadline}")

    # Always reported, never behind a flag: which rounds fed the prediction is
    # part of reading it. A silently narrowed history looks like bad form.
    inc_txt = ", ".join(str(r) for r in sorted(usable)) or "none"
    exc_txt = ", ".join(str(r) for r in excluded) or "none"
    print(f"included: {inc_txt}")
    print(f"excluded: {exc_txt}" + ("  (results not final)" if excluded else ""))

    if not usable:
        why = (f"data_checked is false for: {exc_txt}" if excluded
               else "no earlier gameweeks exist in this snapshot")
        raise SystemExit(
            f"\nNo usable history before GW{target_gw} — {why}.\n"
            "Refusing to predict from provisional data. Nothing was written.\n"
            "Take a fresh snapshot once the previous gameweek is settled."
        )

    blanks = [teams[t] for t in teams if counts.get(t, 0) == 0]
    doubles = [teams[t] for t in teams if counts.get(t, 0) > 1]
    if blanks:
        print(f"blanking:  {', '.join(sorted(blanks))}")
    if doubles:
        print(f"doubles:   {', '.join(sorted(doubles))}")

    rows = []
    for player in bootstrap["elements"]:
        pid = player["id"]
        path = players_dir / f"{pid}.json"
        if not path.exists():
            continue

        history = json.loads(path.read_text()).get("history", [])
        position = positions.get(player["element_type"], "UNK")
        n_fix = counts.get(player["team"], 0)

        predicted, exp_min, pp90 = predict_player(
            player, history, position, n_fix, target_gw, usable
        )

        rows.append({
            "gameweek": target_gw,
            "player_id": pid,
            "web_name": player["web_name"],
            "team": teams.get(player["team"], "UNK"),
            "position": position,
            "price": player["now_cost"] / 10.0,
            "predicted_points": predicted,
            "expected_minutes": exp_min,
            "points_per_90": pp90,
            "n_fixtures": n_fix,
            "status": player.get("status", ""),
        })

    # SPEC section 5: descending predicted_points, ties broken by ascending
    # player_id. The tie-break is not cosmetic — without it the order of tied
    # players falls out of the snapshot's element order, so the same snapshot
    # can produce differently ordered entries and diffs between two model
    # versions read as a reshuffle rather than a change of opinion.
    rows.sort(key=lambda r: (-r["predicted_points"], r["player_id"]))

    outpath = write_entry(
        rows, out_dir, target_gw, MODEL_VERSION, snap.name, deadline
    )

    print(f"\nwrote {len(rows)} predictions -> {outpath}\n")
    print(f"{'player':<18}{'team':<6}{'pos':<5}{'pred':>6}{'mins':>7}{'pp90':>7}")
    print("-" * 49)
    for r in rows[:20]:
        print(f"{r['web_name'][:17]:<18}{r['team']:<6}{r['position']:<5}"
              f"{r['predicted_points']:>6}{r['expected_minutes']:>7}{r['points_per_90']:>7}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gw", type=int, default=None, help="target gameweek (default: next)")
    ap.add_argument(
        "--out",
        default="predictions",
        metavar="DIR",
        help="output directory (default: predictions/). Use a scratch directory "
             "for exploratory runs — predictions/ is the append-only log.",
    )
    args = ap.parse_args()
    main(args.gw, args.out)
