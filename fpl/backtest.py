"""Replay settled gameweeks offline, without leaking the future.

    python -m fpl.backtest                          # baseline-v1, every settled GW
    python -m fpl.backtest --model baseline-v1 --from 2 --to 5
    python -m fpl.backtest --snapshot 20260922T201540Z

For each settled gameweek N in one snapshot, cuts the snapshot back to what was
knowable at GW N's deadline, runs a model on it, and scores the result against
the snapshot's own `live/<N>.json` with the scoring harness's metrics.

## Not evidence

A backtest is tuned on results that are already known. Only committed,
pre-deadline log entries count as a track record. This exists to decide *what
to log*, and every run says so.

## How the future is kept out

`resolve_target_gw` refuses to predict a past gameweek from a later snapshot,
and that refusal stays: real entries never come through here. The backtest is
a separate path that builds its own view of the snapshot, and the view is
leak-free by construction rather than by a model promising not to look:

- **Players** carry only a whitelist of fields. A bootstrap element has over a
  hundred, nearly all describing *now* — form, total points, news, expected
  points, ownership. None of them reach a model.
- **Availability** is not known historically. `status` and
  `chance_of_playing_next_round` describe the snapshot's own next gameweek, so
  every player is marked available. Absolute numbers are therefore optimistic,
  but every model gets the same treatment, so comparisons between models stay
  fair.
- **Price** is the player's `value` in their last round before N, and **club**
  is read from that round's fixture, so a later transfer or price change is
  invisible. That price can trail the deadline's by a price change or two; no
  model reads price, and nothing here scores it.
- **History** is cut to rounds strictly before N. A player with no round before
  N had not joined the game yet and is left out.
- **Fixtures** before N keep their results, which were public. Fixtures in GW N
  lose scores, stats and finished flags. Later fixtures are dropped.
- **Events** after N are dropped, and N is marked as the next gameweek.

Known limitation: a fixture postponed *after* GW N's deadline already sits in
its final gameweek in the snapshot, and FPL's fixture difficulty ratings are
current rather than as-published. Neither can be undone without a snapshot
taken at the time. And "joined the game" is read from history rows, which exist
only for fixtures played: a player whose club's every earlier fixture was
postponed looks unjoined, and lands in the actuals-only bucket rather than
being predicted at zero.
"""

import argparse
import json
import sys
from pathlib import Path

from fpl import metrics, score
from fpl.predict_baseline import predict_rows as baseline_rows
from fpl.snapshot import load_live, load_snapshot, usable_rounds

BACKTEST_VERSION = "backtest-v1"

NOT_EVIDENCE = (
    "A backtest is not evidence: it replays results that are already known. "
    "Only committed, pre-deadline entries count."
)

# Everything a model may know about a player at a past deadline. team and
# now_cost are overwritten from history; status and chance are overwritten
# because they are not known historically.
PLAYER_FIELDS = ("id", "web_name", "element_type", "team", "now_cost")
TEAM_FIELDS = ("id", "name", "short_name")
TYPE_FIELDS = ("id", "singular_name_short")
EVENT_FIELDS = ("id", "deadline_time", "finished", "data_checked")

# A GW N fixture keeps only what was published before it kicked off.
UPCOMING_FIXTURE_FIELDS = (
    "id", "event", "team_h", "team_a", "kickoff_time",
    "team_h_difficulty", "team_a_difficulty",
)

# Naive comparators rebuilt from history. FPL's own form and points_per_game
# are current, so the scoring harness's versions of these would leak here.
RECENT_ROUNDS = 3


def _pick(record, fields):
    return {k: record[k] for k in fields if k in record}


def _club_at(row, fixtures_by_id, fallback):
    """The player's club in a history row, from its fixture and home flag."""
    fixture = fixtures_by_id.get(row.get("fixture"))
    if fixture is None or "was_home" not in row:
        return fallback
    return fixture["team_h"] if row["was_home"] else fixture["team_a"]


def as_of(bootstrap, fixtures, histories, target_gw):
    """The snapshot as it could have been seen at GW `target_gw`'s deadline.

    Returns (bootstrap, fixtures, histories) in the same shapes as the
    originals, so a model reads it exactly as it reads a real snapshot.
    """
    before = {
        pid: [r for r in rows if r.get("round") is not None and r["round"] < target_gw]
        for pid, rows in histories.items()
    }
    fixtures_by_id = {f["id"]: f for f in fixtures}

    elements = []
    for player in bootstrap["elements"]:
        rows = before.get(player["id"])
        if not rows:
            continue  # not in the game yet at this deadline
        last = max(rows, key=lambda r: (r["round"], r.get("kickoff_time") or ""))
        element = _pick(player, PLAYER_FIELDS)
        element["team"] = _club_at(last, fixtures_by_id, player["team"])
        element["now_cost"] = last.get("value", player["now_cost"])
        element["status"] = "a"
        element["chance_of_playing_next_round"] = None
        elements.append(element)

    events = []
    for event in bootstrap["events"]:
        if event["id"] < target_gw:
            events.append(dict(_pick(event, EVENT_FIELDS), is_next=False))
        elif event["id"] == target_gw:
            events.append(dict(_pick(event, EVENT_FIELDS), finished=False,
                               data_checked=False, is_next=True))

    view_fixtures = []
    for fixture in fixtures:
        if fixture.get("event") is None:
            continue
        if fixture["event"] < target_gw:
            view_fixtures.append(dict(fixture))
        elif fixture["event"] == target_gw:
            view_fixtures.append(_pick(fixture, UPCOMING_FIXTURE_FIELDS))

    view = {
        "elements": elements,
        "teams": [_pick(t, TEAM_FIELDS) for t in bootstrap["teams"]],
        "element_types": [_pick(t, TYPE_FIELDS) for t in bootstrap["element_types"]],
        "events": events,
    }
    kept = {e["id"] for e in elements}
    return view, view_fixtures, {pid: rows for pid, rows in before.items() if pid in kept}


def _baseline(bootstrap, fixtures, histories, target_gw):
    usable, _ = usable_rounds(bootstrap["events"], target_gw)
    return baseline_rows(bootstrap, fixtures, histories, target_gw, usable)


# Every model the backtest can replay: name -> f(bootstrap, fixtures,
# histories, target_gw) returning log-shaped rows. A model added here must be
# the same function its log entries come from.
MODELS = {
    "baseline-v1": _baseline,
}


def _comparator_values(histories):
    """Pre-deadline naive predictions per player, from history alone."""
    values = {}
    for pid, rows in histories.items():
        played = [r for r in rows if (r.get("minutes") or 0) > 0]
        recent = sorted(rows, key=lambda r: r["round"], reverse=True)[:RECENT_ROUNDS]
        values[pid] = {
            "zero": 0.0,
            "history_ppg": (
                sum(r.get("total_points", 0) for r in played) / len(played) if played else 0.0),
            f"last_{RECENT_ROUNDS}_mean": (
                sum(r.get("total_points", 0) for r in recent) / len(recent) if recent else 0.0),
        }
    return values


def replay(snap, bootstrap, fixtures, histories, model, target_gw):
    """Predict and score one gameweek. Returns (matched rows, result dict)."""
    view, view_fixtures, view_histories = as_of(bootstrap, fixtures, histories, target_gw)
    rows = MODELS[model](view, view_fixtures, view_histories, target_gw)
    actual = score.actuals(load_live(snap, target_gw))
    matched, missing_actual, missing_prediction = score.join(rows, actual)
    if not matched:
        raise SystemExit(f"GW{target_gw}: no replayed player joins to actuals.")

    pairs = [(p["predicted_points"], p["actual_points"]) for p in matched]
    playing = [(p["predicted_points"], p["actual_points"])
               for p in matched if p["expected_minutes"] > 0]

    naive = _comparator_values(view_histories)
    comparators = {}
    for name in next(iter(naive.values())):
        comparators[name] = metrics.summary(
            [(naive[p["player_id"]][name], p["actual_points"]) for p in matched])

    return matched, {
        "gameweek": target_gw,
        "join": {"matched": len(matched), "missing_actual": len(missing_actual),
                 "missing_prediction": len(missing_prediction)},
        "metrics": {"all": metrics.summary(pairs),
                    "predicted_to_play": metrics.summary(playing) if playing else None},
        "minutes_mae": metrics.mae([(p["expected_minutes"], p["actual_minutes"])
                                    for p in matched]),
        "xi": score.xi_block(matched),
        "comparators": comparators,
    }


def default_gameweeks(bootstrap, manifest):
    """Every settled gameweek from 2 on that has actuals in the snapshot."""
    settled = score.settled_gameweeks(bootstrap["events"])
    live = set(manifest.get("live_gameweeks", []))
    return sorted(gw for gw in settled & live if gw >= 2)


def run(snapshot_id, model, gameweeks=None):
    """Replay `gameweeks` (default: all settled from 2) and pool the results."""
    if model not in MODELS:
        raise SystemExit(f"Unknown model {model!r}. Known: {', '.join(sorted(MODELS))}.")

    snap, bootstrap, fixtures, players_dir = load_snapshot(snapshot_id)
    manifest = json.loads((snap / "manifest.json").read_text())
    if gameweeks is None:
        gameweeks = default_gameweeks(bootstrap, manifest)
    settled = score.settled_gameweeks(bootstrap["events"])
    for gw in gameweeks:
        if gw < 2:
            raise SystemExit("GW1 has no earlier round to predict it from.")
        if gw not in settled:
            raise SystemExit(f"GW{gw} is not settled in {snap.name}; nothing to score it against.")
    if not gameweeks:
        raise SystemExit(f"No settled gameweeks with actuals in {snap.name} to replay.")

    histories = {
        p["id"]: json.loads((players_dir / f"{p['id']}.json").read_text()).get("history", [])
        for p in bootstrap["elements"]
    }

    per_gw, all_matched = [], []
    for gw in gameweeks:
        matched, result = replay(snap, bootstrap, fixtures, histories, model, gw)
        per_gw.append(result)
        all_matched.append(matched)

    pooled = {
        "all": metrics.pooled_summary(
            [[(p["predicted_points"], p["actual_points"]) for p in m] for m in all_matched]),
        "comparators": {
            name: {"mae": sum(r["comparators"][name]["mae"] * r["comparators"][name]["n"]
                              for r in per_gw) / sum(r["comparators"][name]["n"] for r in per_gw)}
            for name in per_gw[0]["comparators"]
        },
    }
    return {
        "backtest_version": BACKTEST_VERSION,
        "note": NOT_EVIDENCE,
        "snapshot_id": snap.name,
        "model_version": model,
        "gameweeks": gameweeks,
        "availability": "every player treated as available; see fpl/backtest.py",
        "per_gameweek": per_gw,
        "pooled": pooled,
    }


def _print(report):
    print(f"BACKTEST — {report['note']}\n")
    print(f"snapshot:  {report['snapshot_id']}")
    print(f"model:     {report['model_version']}")
    print(f"replayed:  {', '.join(f'GW{gw}' for gw in report['gameweeks'])}")
    print("availability: everyone treated as available — optimistic, same for every model\n")
    names = list(report["per_gameweek"][0]["comparators"])
    header = f"{'GW':<6}{'n':>5}{'MAE':>7}{'RMSE':>7}{'rho':>7}{'XI':>9}"
    header += "".join(f"{name:>14}" for name in names)
    print(header)
    for r in report["per_gameweek"]:
        m = r["metrics"]["all"]
        rho = "-" if m["spearman"] is None else f"{m['spearman']:.3f}"
        xi = (f"{r['xi']['captured']['actual']}/{r['xi']['optimum']['actual']}"
              if r["xi"]["available"] else "n/a")
        line = f"GW{r['gameweek']:<4}{m['n']:>5}{m['mae']:>7.2f}{m['rmse']:>7.2f}{rho:>7}{xi:>9}"
        line += "".join(f"{r['comparators'][n]['mae']:>14.2f}" for n in names)
        print(line)
    p = report["pooled"]["all"]
    rho = "-" if p["spearman"] is None else f"{p['spearman']:.3f}"
    line = f"{'pooled':<6}{p['n']:>5}{p['mae']:>7.2f}{p['rmse']:>7.2f}{rho:>7}{'':>9}"
    line += "".join(f"{report['pooled']['comparators'][n]['mae']:>14.2f}" for n in names)
    print(line)
    print("\ncomparator columns are MAE, rebuilt from history — FPL's own form and "
          "points_per_game are current and would leak.")


def main(snapshot_id, model, first, last, out_dir):
    out = Path(out_dir)
    score.refuse_log_output(out)
    gameweeks = None
    if first is not None or last is not None:
        if first is None or last is None:
            raise SystemExit("--from and --to go together.")
        gameweeks = list(range(first, last + 1))

    report = run(snapshot_id, model, gameweeks)
    _print(report)

    out.mkdir(parents=True, exist_ok=True)
    path = out / f"backtest_{report['snapshot_id']}_{model}.json"
    path.write_text(json.dumps(score._round(report), indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--snapshot", default=None, metavar="SNAPSHOT_ID",
                    help="snapshot to replay (default: LATEST)")
    ap.add_argument("--model", default="baseline-v1", choices=sorted(MODELS))
    ap.add_argument("--from", dest="first", type=int, default=None, metavar="GW")
    ap.add_argument("--to", dest="last", type=int, default=None, metavar="GW")
    ap.add_argument("--out", default="scratch", metavar="DIR",
                    help="output directory (default: scratch/, gitignored)")
    args = ap.parse_args()
    sys.exit(main(args.snapshot, args.model, args.first, args.last, args.out))
