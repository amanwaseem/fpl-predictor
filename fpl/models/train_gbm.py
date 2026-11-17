"""Train a gbm version on the past seasons, and say how well it does held out (#85).

    fpl-train-gbm                     # gbm-v2: cross-validate, train, write the artifact
    fpl-train-gbm --cv-only           # the held-out report alone
    fpl-train-gbm --model gbm-v1      # cross-validation only: gbm-v1 is registered

A registered version is fixed from its first entry (ADR 0005), so training
refuses to replace one; --cv-only still reports on it.

Reads `player_gameweek_features` (#84) and each gameweek's actual points and
minutes from the database, for 2016/17-2025/26 only: the live season is held
out whole, for fpl-backtest to judge against baseline-v1 (SPEC section 6).

## Cross-validation

Rolling-origin by season, never shuffled: each of the last three past seasons
is predicted by a model trained on the seasons before it alone, so no fold
sees its own future. Every figure comes with a 95% interval from a bootstrap
over gameweeks (a gameweek's rows move together, so they are resampled
together), and beside a naive comparator on the same rows: recent points per
90 times recent minutes.

## Output

- reports/<version>.md: the held-out report.
- dist/<version>.json: the four trained models, for the release asset.
- fpl/models/registry.json: its SHA-256, URL, training range and git SHA.

Needs the `ml` group (LightGBM) and DATABASE_URL. Run from the repository root.
"""

import argparse
import hashlib
import json
import random
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fpl.evaluation import metrics
from fpl.ingest.history import SEASONS
from fpl.models.feature_table import season_after
from fpl.models.gbm import REGISTRY, VERSIONS, Bundle, vector
from fpl.paths import require_project_root

NEWEST = "gbm-v2"
CV_SEASONS = SEASONS[-3:]
BOOTSTRAP = 500
SEED = 85
ROUNDS = 300
PARAMS: dict[str, Any] = {
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 100,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 4,
    "seed": SEED,
    "verbose": -1,
}
OBJECTIVES: dict[str, dict[str, Any]] = {
    "sixty": {"objective": "binary"},
    "minutes_long": {"objective": "regression"},
    "minutes_short": {"objective": "regression"},
    "points": {"objective": "tweedie", "tweedie_variance_power": 1.3},
}


def training_rows(engine, seasons):
    """[(feature row, labels)] for every feature row with a result, in `seasons`."""
    from sqlalchemy import func, select

    from fpl.db.models import Fixture, PlayerFixtureStats, PlayerGameweekFeatures

    stats = PlayerFixtureStats
    with engine.connect() as c:
        labels = {
            (r.season, r.gameweek, r.element_id): {
                "points": r.points,
                "minutes": r.minutes,
                "sixty": r.most >= 60,
            }
            for r in c.execute(
                select(
                    stats.season,
                    Fixture.gameweek,
                    stats.element_id,
                    func.sum(stats.total_points).label("points"),
                    func.sum(stats.minutes).label("minutes"),
                    func.max(stats.minutes).label("most"),
                )
                .join(
                    Fixture,
                    (Fixture.season == stats.season) & (Fixture.fixture_id == stats.fixture_id),
                )
                .where(stats.season.in_(seasons))
                .group_by(stats.season, Fixture.gameweek, stats.element_id)
            )
        }
        rows = []
        # In a fixed order: bagging samples rows by position, so the artifact is
        # reproducible only if the rows always arrive in the same order.
        features = PlayerGameweekFeatures
        for r in c.execute(
            select(features)
            .where(features.season.in_(seasons))
            .order_by(features.season, features.gameweek, features.element_id)
        ):
            row = dict(r._mapping)
            label = labels.get((row["season"], row["gameweek"], row["element_id"]))
            if label is not None:
                rows.append((row, label))
    return rows


def release(version):
    """(release tag, asset URL, artifact path, report path) for `version`."""
    tag = f"model-{version}"
    url = f"https://github.com/amanwaseem/fpl-predictor/releases/download/{tag}/{version}.json"
    return tag, url, Path(f"dist/{version}.json"), Path(f"reports/{version}.md")


def fit(rows, rounds=ROUNDS, features=VERSIONS[NEWEST]):
    """The four models, trained on `rows`, as model_to_string() texts."""
    import lightgbm
    import numpy

    def train(part, subset, label):
        x = numpy.array(
            [[numpy.nan if v is None else v for v in vector(r, features)] for r, _ in subset]
        )
        y = numpy.array([label(lab) for _, lab in subset], dtype=float)
        booster = lightgbm.train(
            PARAMS | OBJECTIVES[part],
            lightgbm.Dataset(x, label=y, feature_name=features, free_raw_data=True),
            num_boost_round=rounds,
        )
        return booster.model_to_string()

    return {
        "sixty": train("sixty", rows, lambda lab: 1.0 if lab["sixty"] else 0.0),
        "minutes_long": train(
            "minutes_long", [r for r in rows if r[1]["sixty"]], lambda lab: lab["minutes"]
        ),
        "minutes_short": train(
            "minutes_short", [r for r in rows if not r[1]["sixty"]], lambda lab: lab["minutes"]
        ),
        "points": train("points", rows, lambda lab: max(lab["points"], 0)),
    }


def artifact(models, seasons, version=NEWEST):
    import lightgbm

    from fpl.models.feature_table import FEATURE_VERSION

    return {
        "version": version,
        "features": VERSIONS[version],
        "feature_version": FEATURE_VERSION,
        "trained_on": seasons,
        "lightgbm": lightgbm.__version__,
        "params": PARAMS,
        "objectives": OBJECTIVES,
        "rounds": ROUNDS,
        "models": models,
    }


def naive(row):
    """Recent points per 90 times recent minutes: the comparator on the same rows."""
    pp90, minutes = row["points_per90_5"], row["minutes_mean5"]
    if pp90 is None or minutes is None:
        return 0.0
    return pp90 * min(minutes, 90.0) / 90.0 * row["n_fixtures"]


def bootstrap(by_gameweek, statistics, draws=BOOTSTRAP, seed=SEED):
    """A 95% interval for each of `statistics` ({name: f(sample)}), over gameweeks
    resampled with replacement: one set of draws, every statistic on each."""
    rng = random.Random(seed)
    keys = sorted(by_gameweek)
    values: dict[str, list[float]] = {name: [] for name in statistics}
    for _ in range(draws):
        sample = [p for k in (rng.choice(keys) for _ in keys) for p in by_gameweek[k]]
        for name, statistic in statistics.items():
            values[name].append(statistic(sample))
    return {name: _interval(v) for name, v in values.items()}


def _interval(values):
    """The middle 95% of `values`, leaving out draws the statistic had no value for:
    Spearman's rho is None on a resample where one side is constant, and None does
    not sort with numbers (#169). (None, None) unless most draws had one: an
    interval from a handful would be called 95% and mean little."""
    known = sorted(v for v in values if v is not None)
    if len(known) * 2 < len(values):
        return (None, None)
    tail = int(0.025 * len(known))
    return (known[tail], known[len(known) - 1 - tail])


def evaluate(bundle, rows):
    """Held-out figures for `bundle` on `rows`, with the naive comparator and intervals.

    Predicted by fpl.models.trees, as entries will be, rather than by LightGBM:
    the figures are the evaluator's, not only the library's.
    """
    by_gw = defaultdict(list)
    for row, label in rows:
        points, _ = bundle.predict(row)
        by_gw[row["gameweek"]].append((points, naive(row), label["points"]))
    every = [s for v in by_gw.values() for s in v]

    def mae(sample, i):
        return sum(abs(s[i] - s[2]) for s in sample) / len(sample)

    def rho(sample, i):
        return metrics.summary([(s[i], s[2]) for s in sample])["spearman"]

    ci = bootstrap(
        by_gw,
        {
            "mae": lambda s: mae(s, 0),
            "spearman": lambda s: rho(s, 0),
            "gain": lambda s: mae(s, 1) - mae(s, 0),
        },
    )
    return {
        "n": len(every),
        "gameweeks": len(by_gw),
        "gbm": {
            "mae": mae(every, 0),
            "mae_ci": ci["mae"],
            "spearman": rho(every, 0),
            "spearman_ci": ci["spearman"],
            # Mean predicted minus actual: below zero is under-predicting (#174).
            "bias": sum(s[0] - s[2] for s in every) / len(every),
        },
        "naive": {"mae": mae(every, 1), "spearman": rho(every, 1)},
        "mae_gain": mae(every, 1) - mae(every, 0),
        "mae_gain_ci": ci["gain"],
    }


def cross_validate(rows, version=NEWEST):
    folds = {}
    for season in CV_SEASONS:
        train = [r for r in rows if r[0]["season"] < season]
        test = [r for r in rows if r[0]["season"] == season]
        seasons = sorted({r[0]["season"] for r in train})
        bundle = Bundle(artifact(fit(train, features=VERSIONS[version]), seasons, version))
        folds[season] = evaluate(bundle, test)
        print(
            f"{season}: MAE {folds[season]['gbm']['mae']:.3f} "
            f"(naive {folds[season]['naive']['mae']:.3f}), trained on {len(train)} rows"
        )
    return folds


def report(folds, live=None, version=NEWEST):
    def ci(pair):
        return "n/a" if pair[0] is None else f"{pair[0]:.3f} to {pair[1]:.3f}"

    def three(x):
        # Spearman's rho is None where one side is constant (#169).
        return "n/a" if x is None else f"{x:.3f}"

    lines = [
        f"# {version}: held-out report",
        "",
        "Rolling-origin cross-validation by season: each season predicted by a model trained "
        "on the seasons before it alone. 95% intervals from a bootstrap over gameweeks. "
        '"Naive" is recent points per 90 times recent minutes, on the same rows. Lower MAE '
        f"is better; a gain above zero is {version} ahead. Bias is mean predicted minus "
        "actual points: below zero is under-predicting.",
        "",
        f"Not evidence of how {version} predicts: that is the log, from its first entry. "
        "This decides whether to log it (SPEC section 6).",
        "",
        "| Held out | Rows | MAE | MAE 95% | Naive MAE | Gain | Gain 95% "
        "| Spearman | Spearman 95% | Bias |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for season, f in list(folds.items()) + ([("2026/27 so far", live)] if live else []):
        g = f["gbm"]
        lines.append(
            f"| {season} | {f['n']} | {g['mae']:.3f} | {ci(g['mae_ci'])} | "
            f"{f['naive']['mae']:.3f} | {f['mae_gain']:+.3f} | {ci(f['mae_gain_ci'])} | "
            f"{three(g['spearman'])} | {ci(g['spearman_ci'])} | {g['bias']:+.3f} |"
        )
    return "\n".join(lines) + "\n"


def git_sha():
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def main(cv_only=False, version=NEWEST):
    from fpl.db.session import make_engine

    registered = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
    if version in registered and not cv_only:
        raise SystemExit(
            f"{version} is registered, and a version is fixed from its first entry "
            "(ADR 0005). Run with --cv-only for its report, or train a new version."
        )
    tag, url, artifact_path, report_path = release(version)

    engine = make_engine()
    try:
        rows = training_rows(engine, SEASONS)
        live_season = season_after(max(SEASONS))
        live = training_rows(engine, [live_season])
    finally:
        engine.dispose()
    print(
        f"{len(rows)} training rows, {len(SEASONS)} seasons; {len(live)} rows of "
        f"{live_season} held out"
    )
    folds = cross_validate(rows, version)
    if cv_only:
        print(report(folds, version=version))
        return 0

    models = fit(rows, features=VERSIONS[version])
    bundle_data = artifact(models, SEASONS, version)
    live_figures = evaluate(Bundle(bundle_data), live) if live else None
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(report(folds, live_figures, version))
    artifact_path.parent.mkdir(exist_ok=True)
    body = json.dumps(bundle_data, sort_keys=True).encode()
    artifact_path.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    registry = registered
    registry[version] = {
        "sha256": digest,
        "url": url,
        "release": tag,
        "trained_on": SEASONS,
        "feature_version": bundle_data["feature_version"],
        "lightgbm": bundle_data["lightgbm"],
        "git_sha": git_sha(),
        "trained_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "held_out": {
            s: {"mae": f["gbm"]["mae"], "mae_gain": f["mae_gain"]} for s, f in folds.items()
        },
    }
    REGISTRY.parent.mkdir(exist_ok=True)
    REGISTRY.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {artifact_path} (sha256 {digest}), {report_path}, {REGISTRY}")
    print(f"publish: gh release create {tag} {artifact_path}")
    return 0


def cli(argv=None):
    """`fpl-train-gbm`, or `python -m fpl.models.train_gbm`."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cv-only", action="store_true", help="the held-out report, no artifact")
    ap.add_argument("--model", default=NEWEST, choices=sorted(VERSIONS))
    args = ap.parse_args(argv)
    require_project_root()
    return main(args.cv_only, args.model)


if __name__ == "__main__":
    sys.exit(cli())
