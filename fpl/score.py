"""Score every committed prediction against what actually happened.

    python -m fpl.score                         # actuals from data/raw/LATEST
    python -m fpl.score --actuals 20260922T201540Z

The other half of the credibility mechanism (SPEC section 6). For each entry in
`predictions/` whose gameweek has settled, joins the prediction to actual
points from the actuals snapshot's `live/<gw>.json` and writes:

    scores/gw<NN>_<model>.csv    per player: predicted, actual, error, minutes
    scores/gw<NN>_<model>.json   the metrics for that gameweek
    scores/cumulative.json       pooled across gameweeks, per model

## scores/ is regenerable; predictions/ is not

Every run rebuilds the score files from the committed entries plus one
snapshot, and nothing in the output depends on when the run happened, so two
runs produce byte-identical files.

The one input that is not guaranteed to be present is the prediction snapshot
the naive comparators read, since data/raw/ is not shared. When it is absent
the comparators already in the score file are carried forward, provided the
same harness version computed them from the same prediction snapshot. Rerunning
on a fresh clone must not quietly erase numbers it cannot recompute. That is what makes `scores/` safe to rewrite
when a metric definition changes. `HARNESS_VERSION` and both snapshot ids are
stamped into every metrics file so that such a change is visible in the output
rather than a silent rewrite of the track record.

This program opens entries for reading and never writes under `predictions/`.
An output directory named `predictions` is refused outright.

## The join

A player in the entry but absent from actuals is not a zero, and a player in
actuals but absent from the entry is not a model failure. Both are listed by
id, counted, and excluded from every metric. Zeroing either would quietly
flatter or punish the model with nothing in the output to show it.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

from fpl import metrics
from fpl.log import LOG_DIR_NAME, parse_entry_filename, read_entry
from fpl.snapshot import SNAPSHOT_ID, load_live, load_snapshot
from fpl.xi import best_xi

# Bumped whenever a metric's definition changes, so that rewritten score files
# say so.
HARNESS_VERSION = "harness-v1"

# The benchmark every other model is compared against (SPEC section 4).
BASELINE_MODEL = "baseline-v1"

SCORE_FIELDS = [
    "player_id", "web_name", "team", "position",
    "predicted_points", "actual_points", "error",
    "expected_minutes", "actual_minutes",
]

# Mean actual per band of predicted points, over players predicted to play.
# The GW4 review read overconfidence at the top of the table off these bands.
CALIBRATION_BANDS = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, None)]

# FPL fields read from the *prediction* snapshot's bootstrap, so a comparator
# only ever knows what was public before the deadline.
COMPARATORS = {
    "zero": None,
    "points_per_game": "points_per_game",
    "form": "form",
}

DECIMALS = 4


def _round(value):
    """Round floats for output, so reruns cannot differ in the last bit."""
    if isinstance(value, float):
        return round(value, DECIMALS)
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_round(v) for v in value]
    return value


def settled_gameweeks(events):
    """Gameweeks whose results are final: finished and data_checked."""
    return {e["id"] for e in events if e.get("finished") and e.get("data_checked")}


def actuals(live):
    """Per-player actual points and minutes from an event/{gw}/live/ payload.

    `stats.total_points` already sums a double gameweek's two fixtures.
    """
    return {
        e["id"]: {"points": e["stats"]["total_points"],
                  "minutes": e["stats"]["minutes"]}
        for e in live["elements"]
    }


def prediction_bootstrap(snapshot_id):
    """The bootstrap an entry was predicted from, or None if it is not here.

    data/raw/ is gitignored and not shared, so an entry committed months ago
    routinely names a snapshot this machine never had. Only the comparators
    need it; the entry's own score does not, so its absence is reported
    rather than fatal.
    """
    if not SNAPSHOT_ID.match(snapshot_id):
        return None
    path = Path("data/raw") / snapshot_id / "bootstrap.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _block(pairs):
    return metrics.summary(pairs) if pairs else None


def _xi(pool, choose_by):
    """Actual points of the XI chosen by `choose_by`, with what it expected."""
    _, xi, formation = best_xi(pool, choose_by)
    return {
        "actual": sum(p["actual_points"] for p in xi),
        "expected": sum(choose_by(p) for p in xi),
        "formation": formation,
        "player_ids": [p["player_id"] for p in xi],
    }


def _grouped(matched, key):
    groups = {}
    for p in matched:
        groups.setdefault(p[key], []).append(p)
    return {
        name: {
            **metrics.summary([(p["predicted_points"], p["actual_points"]) for p in rows]),
            "predicted_total": sum(p["predicted_points"] for p in rows),
            "actual_total": sum(p["actual_points"] for p in rows),
        }
        for name, rows in sorted(groups.items())
    }


def _calibration(matched):
    playing = [p for p in matched if p["expected_minutes"] > 0]
    bands = []
    for low, high in CALIBRATION_BANDS:
        rows = [p for p in playing if p["predicted_points"] >= low
                and (high is None or p["predicted_points"] < high)]
        if not rows:
            continue
        bands.append({
            "band": f"{low}-{high}" if high is not None else f"{low}+",
            "n": len(rows),
            "mean_predicted": sum(p["predicted_points"] for p in rows) / len(rows),
            "mean_actual": sum(p["actual_points"] for p in rows) / len(rows),
        })
    return bands


def _minutes(matched):
    return {
        "mae": metrics.mae([(p["expected_minutes"], p["actual_minutes"]) for p in matched]),
        "predicted_zero_but_played": sum(
            1 for p in matched if p["expected_minutes"] == 0 and p["actual_minutes"] > 0),
        "predicted_60_plus_but_did_not_play": sum(
            1 for p in matched if p["expected_minutes"] >= 60 and p["actual_minutes"] == 0),
    }


def _comparators(matched, bootstrap):
    """Naive predictors on the same rows, from pre-deadline data only.

    "Beats the baseline" means little until the baseline is shown to beat
    these (the GW4 review's point).
    """
    if bootstrap is None:
        return {"available": False,
                "reason": "prediction snapshot not on this machine"}
    by_id = {e["id"]: e for e in bootstrap["elements"]}
    missing = sorted(p["player_id"] for p in matched if p["player_id"] not in by_id)
    if missing:
        raise SystemExit(
            f"Players {missing[:5]} are in the entry but not in the snapshot it "
            "names. The entry and its snapshot disagree; run fpl.verify_entry."
        )

    out = {"available": True}
    for name, field in COMPARATORS.items():
        def value(p, field=field):
            return 0.0 if field is None else float(by_id[p["player_id"]][field])
        pairs = [(value(p), p["actual_points"]) for p in matched]
        block = metrics.summary(pairs)
        # A constant predictor picks an XI by tie-break alone; its "captured"
        # number would describe player ids, not the predictor.
        if field is not None:
            block["xi_actual"] = _xi(matched, value)["actual"]
        out[name] = block
    return out


def _head_to_head(model_version, matched, baseline_matched):
    if model_version == BASELINE_MODEL:
        return {"applicable": False, "reason": "this is the baseline"}
    if baseline_matched is None:
        return {"applicable": False,
                "reason": f"no {BASELINE_MODEL} entry for this gameweek"}
    base = {p["player_id"]: p for p in baseline_matched}
    common = [p for p in matched if p["player_id"] in base]
    if not common:
        return {"applicable": False, "reason": "no players in common"}
    return {
        "applicable": True,
        "n": len(common),
        "model": metrics.summary([(p["predicted_points"], p["actual_points"]) for p in common]),
        "baseline": metrics.summary(
            [(base[p["player_id"]]["predicted_points"], p["actual_points"]) for p in common]),
    }


def join(rows, actual):
    """Split an entry against actuals into (matched, missing_actual, missing_prediction)."""
    matched, missing_actual = [], []
    for row in rows:
        result = actual.get(row["player_id"])
        if result is None:
            missing_actual.append(row["player_id"])
            continue
        matched.append({
            "player_id": row["player_id"],
            "web_name": row["web_name"],
            "team": row["team"],
            "position": row["position"],
            "predicted_points": row["predicted_points"],
            "actual_points": result["points"],
            "error": row["predicted_points"] - result["points"],
            "expected_minutes": row["expected_minutes"],
            "actual_minutes": result["minutes"],
        })
    logged = {row["player_id"] for row in rows}
    missing_prediction = sorted(pid for pid in actual if pid not in logged)
    return matched, sorted(missing_actual), missing_prediction


def score_entry(rows, gameweek, model_version, events, actual, bootstrap,
                baseline_matched=None, actuals_snapshot_id=None):
    """Score one entry. Returns (matched rows, metrics dict)."""
    if gameweek not in settled_gameweeks(events):
        raise SystemExit(
            f"GW{gameweek} is not settled in the actuals snapshot "
            "(finished and data_checked). Bonus is provisional until then, so "
            "scoring now would score numbers that later change."
        )

    matched, missing_actual, missing_prediction = join(rows, actual)
    if not matched:
        raise SystemExit(f"GW{gameweek} {model_version}: no player joins to actuals.")

    pairs = [(p["predicted_points"], p["actual_points"]) for p in matched]
    playing = [(p["predicted_points"], p["actual_points"])
               for p in matched if p["expected_minutes"] > 0]

    captured = _xi(matched, lambda p: p["predicted_points"])
    optimum = _xi(matched, lambda p: p["actual_points"])

    result = {
        "harness_version": HARNESS_VERSION,
        "gameweek": gameweek,
        "model_version": model_version,
        "prediction_snapshot_id": rows[0]["snapshot_id"],
        "actuals_snapshot_id": actuals_snapshot_id,
        "deadline_utc": rows[0]["deadline_utc"],
        "join": {
            "matched": len(matched),
            "missing_actual": {"count": len(missing_actual), "player_ids": missing_actual},
            "missing_prediction": {"count": len(missing_prediction),
                                   "player_ids": missing_prediction},
        },
        "metrics": {"all": _block(pairs), "predicted_to_play": _block(playing)},
        "totals": {"predicted": sum(p for p, _ in pairs), "actual": sum(a for _, a in pairs)},
        "minutes": _minutes(matched),
        "xi": {
            "captured": captured,
            "optimum": optimum,
            "captured_share": captured["actual"] / optimum["actual"] if optimum["actual"] else None,
        },
        "by_position": _grouped(matched, "position"),
        "by_team": _grouped(matched, "team"),
        "calibration": _calibration(matched),
        "comparators": _comparators(matched, bootstrap),
        "head_to_head": _head_to_head(model_version, matched, baseline_matched),
    }
    return matched, result


def cumulative(scored, events, entries):
    """Pooled figures per model, with pending and missed gameweeks listed.

    A gameweek is missed for a model when it settled after that model's first
    entry and the model has no entry for it. Listing it keeps a gap in the log
    visible instead of letting the record read as unbroken.
    """
    settled = settled_gameweeks(events)
    out = {"harness_version": HARNESS_VERSION, "models": {}}
    models = sorted({model for _, model in entries})
    for model in models:
        logged = sorted(gw for gw, m in entries if m == model)
        mine = sorted((gw, matched, res) for (gw, m), (matched, res) in scored.items()
                      if m == model)
        all_pairs = [[(p["predicted_points"], p["actual_points"]) for p in matched]
                     for _, matched, _ in mine]
        playing_pairs = [[(p["predicted_points"], p["actual_points"])
                          for p in matched if p["expected_minutes"] > 0]
                         for _, matched, _ in mine]
        minutes = [(p["expected_minutes"], p["actual_minutes"])
                   for _, matched, _ in mine for p in matched]
        captured = sum(res["xi"]["captured"]["actual"] for _, _, res in mine)
        optimum = sum(res["xi"]["optimum"]["actual"] for _, _, res in mine)
        last_settled = max(settled) if settled else 0
        out["models"][model] = {
            "gameweeks_scored": [gw for gw, _, _ in mine],
            "gameweeks_pending": [gw for gw in logged if gw not in settled],
            "gameweeks_missed": [gw for gw in range(logged[0], last_settled + 1)
                                 if gw in settled and gw not in logged],
            "pooled": {
                "all": metrics.pooled_summary(all_pairs) if mine else None,
                "predicted_to_play": (metrics.pooled_summary(playing_pairs)
                                      if any(playing_pairs) else None),
                "minutes_mae": metrics.mae(minutes) if minutes else None,
            },
            "xi": {"captured": captured, "optimum": optimum,
                   "captured_share": captured / optimum if optimum else None},
            "per_gameweek": [
                {"gameweek": gw, **{k: res["metrics"]["all"][k]
                                    for k in ("n", "mae", "rmse", "bias", "spearman")}}
                for gw, _, res in mine
            ],
        }
    return out


def _write_json(path, payload):
    path.write_text(json.dumps(_round(payload), indent=2, sort_keys=True) + "\n")


def _write_scores_csv(path, matched):
    rows = sorted(matched, key=lambda p: (-p["predicted_points"], p["player_id"]))
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SCORE_FIELDS)
        writer.writeheader()
        for p in rows:
            writer.writerow({**p, "error": round(p["error"], 2)})


def find_entries(log_dir):
    """Every entry in the log as {(gameweek, model): path}. Bad names are fatal."""
    entries, bad = {}, []
    for path in sorted(Path(log_dir).glob("*.csv")):
        try:
            entries[parse_entry_filename(path)] = path
        except ValueError as e:
            bad.append(str(e).splitlines()[0])
    if bad:
        raise SystemExit("Files in the log that are not entries:\n  " + "\n  ".join(bad))
    return entries


def _carried_comparators(out, gw, model, result):
    """Comparators from the existing score file, when they can't be recomputed.

    Only carried when the same harness version computed them from the same
    prediction snapshot, so a carried block always means exactly what a
    recomputed one would. Otherwise the gap is reported, not hidden.
    """
    stem = f"gw{gw:02d}_{model}"
    path = out / f"{stem}.json"
    previous = json.loads(path.read_text()) if path.exists() else None
    if (previous is not None
            and previous.get("harness_version") == HARNESS_VERSION
            and previous.get("prediction_snapshot_id") == result["prediction_snapshot_id"]
            and previous.get("comparators", {}).get("available")):
        print(f"carried:   {stem} comparators — prediction snapshot "
              f"{result['prediction_snapshot_id']} not on this machine")
        return previous["comparators"]
    print(f"WARNING:   {stem} has no comparators — prediction snapshot "
          f"{result['prediction_snapshot_id']} not on this machine and none to carry")
    return result["comparators"]


def main(actuals_id, out_dir, log_dir=LOG_DIR_NAME):
    out = Path(out_dir)
    # Compared against the log directory itself, not by name anywhere in the
    # path: a clone living under ~/work/predictions/ must still be able to
    # write scores/. The name check catches a second copy of the log reached
    # some other way.
    log = Path(log_dir).resolve()
    if (out.resolve() == log or out.resolve().is_relative_to(log)
            or out.resolve().name == LOG_DIR_NAME):
        raise SystemExit(
            f"Refusing to write score files into {out}: predictions/ is the "
            "append-only log, and the harness never writes there."
        )

    entries = find_entries(log_dir)
    if not entries:
        raise SystemExit(f"No entries in {log_dir}/ — nothing to score.")

    snap, bootstrap, _, _ = load_snapshot(actuals_id)
    events = bootstrap["events"]
    settled = settled_gameweeks(events)

    print(f"actuals:   {snap.name}")
    print(f"harness:   {HARNESS_VERSION}")

    # Baseline first, so every other model's head-to-head can see it.
    order = sorted(entries, key=lambda k: (k[0], k[1] != BASELINE_MODEL, k[1]))
    scored, live_cache = {}, {}
    for gw, model in order:
        path = entries[(gw, model)]
        if gw not in settled:
            print(f"pending:   GW{gw} {model} — results not final")
            continue
        rows, faults = read_entry(path)
        if faults:
            raise SystemExit(f"{path} does not read as an entry:\n  " + "\n  ".join(faults))
        if gw not in live_cache:
            live_cache[gw] = actuals(load_live(snap, gw))
        baseline = scored.get((gw, BASELINE_MODEL), (None,))[0]
        matched, result = score_entry(
            rows, gw, model, events, live_cache[gw],
            prediction_bootstrap(rows[0]["snapshot_id"]),
            baseline_matched=baseline, actuals_snapshot_id=snap.name,
        )
        if not result["comparators"]["available"]:
            result["comparators"] = _carried_comparators(out, gw, model, result)
        scored[(gw, model)] = (matched, result)

    out.mkdir(parents=True, exist_ok=True)
    for (gw, model), (matched, result) in scored.items():
        stem = f"gw{gw:02d}_{model}"
        _write_scores_csv(out / f"{stem}.csv", matched)
        _write_json(out / f"{stem}.json", result)
    summary = cumulative(scored, events, list(entries))
    _write_json(out / "cumulative.json", summary)

    print(f"\n{'entry':<22}{'n':>5}{'MAE':>7}{'RMSE':>7}{'rho':>7}{'XI':>9}"
          f"{'  vs form MAE':>14}")
    for (gw, model), (_, r) in sorted(scored.items()):
        m = r["metrics"]["all"]
        rho = "-" if m["spearman"] is None else f"{m['spearman']:.3f}"
        xi = f"{r['xi']['captured']['actual']}/{r['xi']['optimum']['actual']}"
        comp = r["comparators"]
        form = f"{comp['form']['mae']:.2f}" if comp["available"] else "n/a"
        print(f"{f'gw{gw:02d}_{model}':<22}{m['n']:>5}{m['mae']:>7.2f}{m['rmse']:>7.2f}"
              f"{rho:>7}{xi:>9}{form:>14}")
    for model, block in summary["models"].items():
        if block["gameweeks_missed"]:
            missed = ", ".join(f"GW{gw}" for gw in block["gameweeks_missed"])
            print(f"missed:    {model} — {missed} (not predicted)")
    print(f"\nwrote {len(scored)} scored entr{'y' if len(scored) == 1 else 'ies'} -> {out}/")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--actuals", default=None, metavar="SNAPSHOT_ID",
                    help="snapshot holding the actual results (default: LATEST)")
    ap.add_argument("--out", default="scores", metavar="DIR",
                    help="output directory (default: scores/)")
    args = ap.parse_args()
    sys.exit(main(args.actuals, args.out))
