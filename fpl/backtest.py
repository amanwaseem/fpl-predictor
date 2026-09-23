"""Replay settled gameweeks offline, without leaking the future.

    python -m fpl.backtest                          # baseline-v1, every settled GW
    python -m fpl.backtest --model baseline-v1 --from 2 --to 5
    python -m fpl.backtest --snapshot 20260922T201540Z
    python -m fpl.backtest --model baseline-prior --held-out

For each settled gameweek N in one snapshot, cuts the snapshot back to what was
knowable at GW N's deadline, runs a model on it, and scores the result against
the snapshot's own `live/<N>.json` with the scoring harness's metrics.

## Not evidence

A backtest is tuned on results that are already known. Only committed,
pre-deadline log entries count as a track record. This exists to decide *what
to log*, and every run says so.

## Held out

A table from `run` is tuned and scored on the same weeks, which is how a
factor can look better while getting worse. `--held-out` re-tunes a model
over its grid (GRIDS) once per settled gameweek, choosing the setting with the
lowest pooled MAE on the *other* weeks, and scores the left-out week with it.
The pooled held-out figures are what tuning on the backtest is actually worth,
and they are the gate for keeping a factor (SPEC section 6).

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
  model reads price, and nothing here scores it. A row missing the fields
  these come from stops the run rather than falling back to today's club.
- **History** is cut to rounds strictly before N. A player with no round before
  N had not joined the game yet and is left out.
- **Past seasons** (`history_past`) pass through whole. They are season totals
  for seasons that ended before this one began, so every row was public at
  every deadline this season.
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
import itertools
import json
import math
import sys
from pathlib import Path

from fpl import metrics, score
from fpl.candidates import baseline_fixture_rows, baseline_prior_rows
from fpl.features import player_prior, previous_season
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


# Read off each player's last row before the cut. Without them the only source
# of club and price is the current bootstrap, which is post-deadline.
HISTORY_FIELDS = ("fixture", "was_home", "value")


def _as_of_row(player_id, row, fixtures_by_id):
    """(club, price) from a history row. Refuses rather than fall back.

    Falling back to the bootstrap's team and now_cost would hand the replay a
    later transfer — and that club's fixture count — and a later price, with
    nothing in the output to show it. The FPL API is unversioned; if these
    fields ever move, the backtest has to stop, not quietly leak.
    """
    missing = [f for f in HISTORY_FIELDS if f not in row]
    if missing:
        raise SystemExit(
            f"Player {player_id}'s round-{row.get('round')} history row has no "
            f"{', '.join(missing)}. The replay reads club and price from it; the "
            "only other source is the current bootstrap, which would leak. "
            "Has the element-summary schema changed?"
        )
    fixture = fixtures_by_id.get(row["fixture"])
    if fixture is None:
        raise SystemExit(
            f"Player {player_id}'s round-{row['round']} history row names fixture "
            f"{row['fixture']}, which is not in fixtures.json, so his club at the "
            "time cannot be read. Refusing to fall back to his current club."
        )
    club = fixture["team_h"] if row["was_home"] else fixture["team_a"]
    return club, row["value"]


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
        element["team"], element["now_cost"] = _as_of_row(player["id"], last, fixtures_by_id)
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


def _baseline(bootstrap, fixtures, histories, target_gw, pasts=None):
    usable, _ = usable_rounds(bootstrap["events"], target_gw)
    return baseline_rows(bootstrap, fixtures, histories, target_gw, usable)


# Every model the backtest can replay: name -> f(bootstrap, fixtures,
# histories, target_gw, pasts) returning log-shaped rows. A logged model added
# here must be the same function its log entries come from; a candidate from
# fpl/candidates.py has no log entries and is never logged.
MODELS = {
    "baseline-v1": _baseline,
    "baseline-prior": baseline_prior_rows,
    "baseline-fixture": baseline_fixture_rows,
    "baseline-fdr": lambda *view: baseline_fixture_rows(*view, strength="fdr"),
    # #30 and #31 together: whether the two ideas add up, ahead of #32.
    "baseline-prior-fixture": lambda *view: baseline_fixture_rows(*view, last_season=True),
}


# Settings the held-out search may tune, per model: name -> values tried.
# Keyword arguments to the model function. A model without an entry is scored
# as it stands. Values are listed rather than ranged so a grid reads as the
# claim it is: these, and only these, were tried.
GRIDS = {
    "baseline-prior": {
        "floor": (900, 1800, 2700),
        "player_prior_minutes": (450, 900, 1800),
        "xg_weight": (0.0, 0.5, 1.0),
    },
}


def settings(model):
    """Every combination in the model's grid, in a fixed order. [{}] if none."""
    grid = GRIDS.get(model, {})
    names = sorted(grid)
    return [dict(zip(names, values))
            for values in itertools.product(*(grid[n] for n in names))]


def _comparator_values(histories):
    """Pre-deadline naive predictions per player, from history alone."""
    values = {}
    for pid, rows in histories.items():
        played = [r for r in rows if (r.get("minutes") or 0) > 0]
        # Per round, not per row: a double gameweek is two rows and one round,
        # and averaging its fixtures separately would halve a double's haul and
        # stretch the window back past the rounds it claims to cover.
        by_round = {}
        for r in rows:
            by_round[r["round"]] = by_round.get(r["round"], 0) + r.get("total_points", 0)
        recent = [by_round[rnd] for rnd in sorted(by_round, reverse=True)[:RECENT_ROUNDS]]
        values[pid] = {
            "zero": 0.0,
            "history_ppg": (
                sum(r.get("total_points", 0) for r in played) / len(played) if played else 0.0),
            f"last_{RECENT_ROUNDS}_mean": sum(recent) / len(recent) if recent else 0.0,
        }
    return values


def prior_coverage(view, pasts):
    """How many players in the view have a usable last season, and how many not."""
    season = previous_season(view["events"])
    player = sum(1 for e in view["elements"]
                 if player_prior(pasts.get(e["id"], []), season) is not None)
    return {"player": player, "positional": len(view["elements"]) - player}


def club_residuals(matched):
    """Predicted minus actual, summed per club over players predicted to play.

    Hauls are team events: a model blind to the opponent misses whole clubs at
    once, one way or the other. The spread of these sums across clubs is how
    much of the week's error came club-shaped.
    """
    sums = {}
    for p in matched:
        if p["expected_minutes"] > 0:
            sums[p["team"]] = sums.get(p["team"], 0.0) + p["predicted_points"] - p["actual_points"]
    return sums


def spread(values):
    """Population standard deviation. Removes the week's overall bias."""
    if not values:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def replay(snap, bootstrap, fixtures, histories, pasts, model, target_gw):
    """Predict and score one gameweek. Returns (matched rows, result dict)."""
    view, view_fixtures, view_histories = as_of(bootstrap, fixtures, histories, target_gw)
    view_pasts = {pid: pasts.get(pid, []) for pid in view_histories}
    rows = MODELS[model](view, view_fixtures, view_histories, target_gw, view_pasts)
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
        "prior_coverage": prior_coverage(view, view_pasts),
        "club_residuals": club_residuals(matched),
        "club_spread": spread(list(club_residuals(matched).values())),
    }


def default_gameweeks(bootstrap, manifest):
    """Every settled gameweek from 2 on that has actuals in the snapshot."""
    settled = score.settled_gameweeks(bootstrap["events"])
    live = set(manifest.get("live_gameweeks", []))
    return sorted(gw for gw in settled & live if gw >= 2)


def _load(snapshot_id, model, gameweeks):
    """Snapshot, histories and the checked list of gameweeks to replay."""
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

    histories, pasts = {}, {}
    for p in bootstrap["elements"]:
        summary = json.loads((players_dir / f"{p['id']}.json").read_text())
        histories[p["id"]] = summary.get("history", [])
        pasts[p["id"]] = summary.get("history_past", [])
    return snap, bootstrap, fixtures, histories, pasts, gameweeks


def run(snapshot_id, model, gameweeks=None):
    """Replay `gameweeks` (default: all settled from 2) and pool the results."""
    snap, bootstrap, fixtures, histories, pasts, gameweeks = _load(snapshot_id, model, gameweeks)

    per_gw, all_matched = [], []
    for gw in gameweeks:
        matched, result = replay(snap, bootstrap, fixtures, histories, pasts, model, gw)
        per_gw.append(result)
        all_matched.append(matched)

    # Root mean square of each week's spread: every week's clubs centred on
    # that week, so one week's overall bias does not read as club error.
    spreads = [r["club_spread"] for r in per_gw if r["club_spread"] is not None]
    pooled = {
        "all": metrics.pooled_summary(
            [[(p["predicted_points"], p["actual_points"]) for p in m] for m in all_matched]),
        "club_spread": (math.sqrt(sum(x * x for x in spreads) / len(spreads))
                        if spreads else None),
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


def _pairs(matched):
    return [(p["predicted_points"], p["actual_points"]) for p in matched]


def held_out(snapshot_id, model, gameweeks=None):
    """Leave-one-gameweek-out: tune on the other weeks, score the one left out.

    Every setting in the model's grid is replayed on every gameweek once, from
    the same leak-free view `run` uses. Then, for each gameweek, the setting
    with the lowest pooled MAE on the remaining weeks is chosen — the held-out
    week's actuals play no part in its own choice — and that week is scored
    with it. Ties go to the earlier setting in grid order, so a rerun chooses
    the same.
    """
    snap, bootstrap, fixtures, histories, pasts, gameweeks = _load(snapshot_id, model, gameweeks)
    if len(gameweeks) < 2:
        raise SystemExit("Held out needs two or more gameweeks: one to score, one to tune on.")

    grid = settings(model)
    matched = {}  # (setting index, gameweek) -> matched rows
    for gw in gameweeks:
        view, view_fixtures, view_histories = as_of(bootstrap, fixtures, histories, gw)
        view_pasts = {pid: pasts.get(pid, []) for pid in view_histories}
        actual = score.actuals(load_live(snap, gw))
        for i, setting in enumerate(grid):
            rows = MODELS[model](view, view_fixtures, view_histories, gw, view_pasts, **setting)
            matched[i, gw], _, _ = score.join(rows, actual)
            if not matched[i, gw]:
                raise SystemExit(f"GW{gw}: no replayed player joins to actuals.")

    folds = []
    for gw in gameweeks:
        train = [g for g in gameweeks if g != gw]
        best = min(range(len(grid)), key=lambda i: (
            metrics.pooled_summary([_pairs(matched[i, g]) for g in train])["mae"], i))
        folds.append({
            "gameweek": gw,
            "chosen": grid[best],
            "tuned_on": train,
            "metrics": metrics.summary(_pairs(matched[best, gw])),
        })

    return {
        "backtest_version": BACKTEST_VERSION,
        "note": NOT_EVIDENCE,
        "method": "leave-one-gameweek-out; each week scored by the setting with "
                  "the lowest pooled MAE on the others",
        "snapshot_id": snap.name,
        "model_version": model,
        "gameweeks": gameweeks,
        "grid": GRIDS.get(model, {}),
        "folds": folds,
        "pooled": metrics.pooled_summary(
            [_pairs(matched[grid.index(f["chosen"]), f["gameweek"]]) for f in folds]),
    }


def _print_held_out(report):
    print(f"BACKTEST, HELD OUT — {report['note']}\n")
    print(f"snapshot:  {report['snapshot_id']}")
    print(f"model:     {report['model_version']}")
    grid = report["grid"]
    print("grid:      " + ("; ".join(f"{k} {', '.join(map(str, v))}" for k, v in sorted(grid.items()))
                           if grid else "none — scored as it stands"))
    print(f"method:    {report['method']}\n")
    print(f"{'GW':<6}{'n':>5}{'MAE':>7}{'RMSE':>7}{'rho':>7}   chosen on the other weeks")
    for f in report["folds"]:
        m = f["metrics"]
        rho = "-" if m["spearman"] is None else f"{m['spearman']:.3f}"
        chosen = ", ".join(f"{k}={v}" for k, v in sorted(f["chosen"].items())) or "-"
        print(f"GW{f['gameweek']:<4}{m['n']:>5}{m['mae']:>7.3f}{m['rmse']:>7.3f}{rho:>7}   {chosen}")
    p = report["pooled"]
    rho = "-" if p["spearman"] is None else f"{p['spearman']:.3f}"
    print(f"{'pooled':<6}{p['n']:>5}{p['mae']:>7.3f}{p['rmse']:>7.3f}{rho:>7}")


def _print(report):
    print(f"BACKTEST — {report['note']}\n")
    print(f"snapshot:  {report['snapshot_id']}")
    print(f"model:     {report['model_version']}")
    print(f"replayed:  {', '.join(f'GW{gw}' for gw in report['gameweeks'])}")
    print("availability: everyone treated as available — optimistic, same for every model\n")
    names = list(report["per_gameweek"][0]["comparators"])
    header = f"{'GW':<6}{'n':>5}{'MAE':>7}{'RMSE':>7}{'rho':>7}{'XI':>9}{'club sd':>9}"
    header += "".join(f"{name:>14}" for name in names)
    print(header)
    for r in report["per_gameweek"]:
        m = r["metrics"]["all"]
        rho = "-" if m["spearman"] is None else f"{m['spearman']:.3f}"
        xi = (f"{r['xi']['captured']['actual']}/{r['xi']['optimum']['actual']}"
              if r["xi"]["available"] else "n/a")
        club = "-" if r["club_spread"] is None else f"{r['club_spread']:.1f}"
        line = f"GW{r['gameweek']:<4}{m['n']:>5}{m['mae']:>7.2f}{m['rmse']:>7.2f}{rho:>7}{xi:>9}"
        line += f"{club:>9}"
        line += "".join(f"{r['comparators'][n]['mae']:>14.2f}" for n in names)
        print(line)
    p = report["pooled"]["all"]
    rho = "-" if p["spearman"] is None else f"{p['spearman']:.3f}"
    club = report["pooled"]["club_spread"]
    club = "-" if club is None else f"{club:.1f}"
    line = f"{'pooled':<6}{p['n']:>5}{p['mae']:>7.2f}{p['rmse']:>7.2f}{rho:>7}{'':>9}{club:>9}"
    line += "".join(f"{report['pooled']['comparators'][n]['mae']:>14.2f}" for n in names)
    print(line)
    print("\ncomparator columns are MAE, rebuilt from history — FPL's own form and "
          "points_per_game are current and would leak.")
    print("club sd:   spread across clubs of predicted minus actual, summed over players "
          "predicted to play.")
    # Printed for every model, not only those that read it: it is a fact about
    # the snapshot, and it says how much of a prior-based result is the prior.
    print("prior:     " + ", ".join(
        f"GW{r['gameweek']} {r['prior_coverage']['player']} last season / "
        f"{r['prior_coverage']['positional']} positional"
        for r in report["per_gameweek"]))


def main(snapshot_id, model, first, last, out_dir, held=False):
    out = Path(out_dir)
    score.refuse_log_output(out)
    gameweeks = None
    if first is not None or last is not None:
        if first is None or last is None:
            raise SystemExit("--from and --to go together.")
        gameweeks = list(range(first, last + 1))

    if held:
        report = held_out(snapshot_id, model, gameweeks)
        _print_held_out(report)
        name = f"backtest_heldout_{report['snapshot_id']}_{model}.json"
    else:
        report = run(snapshot_id, model, gameweeks)
        _print(report)
        name = f"backtest_{report['snapshot_id']}_{model}.json"

    out.mkdir(parents=True, exist_ok=True)
    path = out / name
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
    ap.add_argument("--held-out", action="store_true",
                    help="tune on all but one gameweek, score that one, for each; "
                         "the gate for keeping a factor")
    args = ap.parse_args()
    sys.exit(main(args.snapshot, args.model, args.first, args.last, args.out, args.held_out))
