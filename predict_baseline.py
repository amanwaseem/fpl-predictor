"""Rolling-form baseline predictor.

Deliberately simple: no training, no ML. Predicts a player's points for the
target gameweek from their recent minutes and scoring rate, adjusted for
availability and fixture count.

This is the number every future model has to beat. If a component model can't
outperform "what did they do recently", it isn't earning its complexity.

Writes an immutable prediction log entry to predictions/. That file gets
committed BEFORE the deadline and is never rewritten.

Usage:
    python predict_baseline.py              # predict the next gameweek
    python predict_baseline.py --gw 4       # predict a specific gameweek
    python predict_baseline.py --out scratch/   # exploratory run, not the log
"""

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

MODEL_VERSION = "baseline-v1"

# Exponential decay over recent gameweeks, most recent first.
# Roughly a half-life of two gameweeks.
DECAY = 0.7
LOOKBACK = 5

# Shrinkage: pseudo-minutes of league-average scoring blended into every
# player's rate. Stops 12-minute cameos dominating the top of the table.
PRIOR_MINUTES = 270.0

# Rough points-per-90 for a regular starter, by position.
POSITION_PRIOR_PP90 = {"GKP": 3.2, "DEF": 3.3, "MID": 3.6, "FWD": 3.8}

# The prediction log schema, in SPEC section 5 order. Declared rather than
# inferred from the first row: the column set is part of the log contract, and
# every gameweek has to stay comparable to the ones already committed.
FIELDS = [
    "gameweek", "player_id", "web_name", "team", "position", "price",
    "predicted_points", "expected_minutes", "points_per_90", "n_fixtures",
    "status", "model_version", "snapshot_id", "generated_at_utc", "deadline_utc",
]


def load_snapshot():
    """Load the most recent raw snapshot written by fetch_fpl.py."""
    latest_file = Path("data/raw/LATEST")
    if not latest_file.exists():
        raise SystemExit("No snapshot found. Run: python fetch_fpl.py")

    snap = Path("data/raw") / latest_file.read_text().strip()
    players_dir = snap / "players"
    if not players_dir.exists():
        raise SystemExit(
            f"Snapshot {snap} has no per-player history.\n"
            "It was probably taken with --skip-players. Run: python fetch_fpl.py"
        )

    manifest_path = snap / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(
            f"Snapshot {snap} has no manifest.json — it is incomplete.\n"
            "An interrupted fetch leaves a directory that looks finished but is "
            "missing players, and those players would be dropped from the "
            "prediction log silently.\n"
            "Resume it with: python fetch_fpl.py --resume"
        )

    manifest = json.loads(manifest_path.read_text())
    bootstrap = json.loads((snap / "bootstrap.json").read_text())
    fixtures = json.loads((snap / "fixtures.json").read_text())

    # Belt and braces: the manifest says the fetch finished, but verify the
    # files are actually on disk before building a log entry from them. A
    # prediction missing an arbitrary subset of players is worse than no
    # prediction, because nothing in the committed CSV reveals the gap.
    missing = [p["id"] for p in bootstrap["elements"]
               if not (players_dir / f"{p['id']}.json").exists()]
    if missing:
        shown = ", ".join(str(i) for i in missing[:10])
        more = f" (and {len(missing) - 10} more)" if len(missing) > 10 else ""
        raise SystemExit(
            f"Snapshot {snap} claims {manifest.get('element_count')} players but "
            f"{len(missing)} history files are missing: {shown}{more}.\n"
            "Refusing to write a partial prediction log.\n"
            "Resume it with: python fetch_fpl.py --resume"
        )

    return snap, bootstrap, fixtures, players_dir


def resolve_target_gw(events, requested):
    """Pick the gameweek to predict, and return it with its deadline.

    Refuses to target a gameweek earlier than the snapshot's own next one.
    usable_rounds bounds *history* to rounds before the target, but the
    bootstrap fields are not bounded: chance_of_playing_next_round means "next
    round as of this snapshot", and now_cost and status are snapshot-time too.
    Predicting forward is fine. Backtesting GW4 from a later snapshot would
    feed post-deadline availability and price into the model — a rule 2
    violation that leaves no trace in the output.
    """
    upcoming = next((e for e in events if e.get("is_next")), None)
    if requested is not None:
        event = next((e for e in events if e["id"] == requested), None)
        if event is None:
            raise SystemExit(f"No gameweek {requested} in this snapshot.")
        if upcoming is not None and requested < upcoming["id"]:
            raise SystemExit(
                f"Refusing to predict GW{requested} from a snapshot whose next "
                f"gameweek is GW{upcoming['id']}.\n"
                "Availability, price and status in this snapshot are all "
                "post-deadline for GW{}, so the prediction would be "
                "contaminated.\n"
                "Backtesting needs a snapshot taken before that deadline."
                .format(requested)
            )
    else:
        event = next((e for e in events if e.get("is_next")), None)
        if event is None:
            raise SystemExit("No upcoming gameweek found — season may be over.")
    return event["id"], event["deadline_time"]


def fixture_counts(fixtures, target_gw):
    """How many fixtures each team has in the target gameweek.

    Almost always 1, but blanks (0) and doubles (2) happen and are the most
    common source of badly wrong predictions.
    """
    counts = {}
    for f in fixtures:
        if f.get("event") != target_gw:
            continue
        for side in ("team_h", "team_a"):
            counts[f[side]] = counts.get(f[side], 0) + 1
    return counts


def usable_rounds(events, target_gw):
    """Split rounds before the target into (usable, excluded).

    A round is usable only once its results are final. The API reports that as
    data_checked, which flips true when bonus points are settled.

    A snapshot taken mid-gameweek still carries a history row for every player,
    including those whose fixture has not kicked off. That row reads 0 minutes
    and 0 points and is indistinguishable from a genuine non-appearance, so
    including it silently scores an unplayed match as a benching.
    """
    before = [e for e in events if e["id"] < target_gw]
    usable = {e["id"] for e in before if e.get("data_checked")}
    excluded = sorted(e["id"] for e in before if not e.get("data_checked"))
    return usable, excluded


def recent_history(history, target_gw, usable):
    """Recent gameweeks, most recent first, aggregated per round.

    Doubles produce two rows for one round, so sum them. Only rounds strictly
    before the target are used — never look at the gameweek being predicted —
    and only rounds whose results are final. The target check is redundant with
    `usable` by construction and kept anyway: a leak here invalidates the whole
    track record.
    """
    by_round = {}
    for h in history:
        rnd = h.get("round")
        if rnd is None or rnd >= target_gw or rnd not in usable:
            continue
        entry = by_round.setdefault(rnd, {"minutes": 0, "points": 0})
        entry["minutes"] += h.get("minutes", 0) or 0
        entry["points"] += h.get("total_points", 0) or 0

    ordered = sorted(by_round.items(), key=lambda kv: kv[0], reverse=True)
    return [v for _, v in ordered[:LOOKBACK]]


def weighted(values):
    """Exponentially decayed mean. Input is most-recent-first."""
    if not values:
        return 0.0, 0.0
    total = weight_sum = 0.0
    for i, v in enumerate(values):
        w = DECAY ** i
        total += v * w
        weight_sum += w
    return total / weight_sum, weight_sum


def availability(player):
    """Fraction of normal minutes expected, from injury/suspension flags.

    status: a=available, d=doubtful, i=injured, s=suspended, u=unavailable
    """
    chance = player.get("chance_of_playing_next_round")
    if chance is not None:
        return chance / 100.0
    return 1.0 if player.get("status") == "a" else 0.0


def predict_player(player, history, position, n_fixtures, target_gw, usable):
    """Expected points for one player in the target gameweek."""
    if n_fixtures == 0:
        return 0.0, 0.0, 0.0  # blank gameweek

    recent = recent_history(history, target_gw, usable)
    if not recent:
        return 0.0, 0.0, 0.0  # no appearances to reason from

    minutes_list = [r["minutes"] for r in recent]
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

    rows.sort(key=lambda r: r["predicted_points"], reverse=True)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    outdir = Path(out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    outpath = outdir / f"gw{target_gw:02d}_{MODEL_VERSION}.csv"

    if outpath.exists():
        raise SystemExit(
            f"{outpath} already exists.\n"
            "The prediction log is append-only — entries are never rewritten. "
            "Delete it manually only if it was never committed."
        )

    with outpath.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for r in rows:
            r.update({
                "model_version": MODEL_VERSION,
                "snapshot_id": snap.name,
                "generated_at_utc": generated_at,
                "deadline_utc": deadline,
            })
            writer.writerow(r)

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
