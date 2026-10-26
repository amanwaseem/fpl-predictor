"""Train gbm-v1 on the past seasons, and say how well it does held out (#85).

    fpl-train-gbm                     # cross-validate, train, write the artifact
    fpl-train-gbm --cv-only           # the held-out report alone

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

- reports/gbm-v1.md: the held-out report.
- dist/gbm-v1.json: the four trained models, for the release asset.
- models/registry.json: its SHA-256, URL, training range and git SHA.

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

from fpl.evaluation import metrics
from fpl.ingest.history import SEASONS
from fpl.models.gbm import FEATURES, MODEL_VERSION, REGISTRY, Bundle, vector
from fpl.paths import require_project_root

RELEASE = "model-gbm-v1"
URL = f"https://github.com/amanwaseem/fpl-predictor/releases/download/{RELEASE}/gbm-v1.json"
ARTIFACT = Path("dist/gbm-v1.json")
REPORT = Path("reports/gbm-v1.md")
CV_SEASONS = SEASONS[-3:]
BOOTSTRAP = 500
SEED = 85
ROUNDS = 300
PARAMS = {
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
OBJECTIVES = {
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
        for r in c.execute(
            select(PlayerGameweekFeatures).where(PlayerGameweekFeatures.season.in_(seasons))
        ):
            row = dict(r._mapping)
            label = labels.get((row["season"], row["gameweek"], row["element_id"]))
            if label is not None:
                rows.append((row, label))
    return rows


def fit(rows, rounds=ROUNDS):
    """The four models, trained on `rows`, as model_to_string() texts."""
    import lightgbm
    import numpy

    def train(part, subset, label):
        x = numpy.array([[numpy.nan if v is None else v for v in vector(r)] for r, _ in subset])
        y = numpy.array([label(lab) for _, lab in subset], dtype=float)
        booster = lightgbm.train(
            PARAMS | OBJECTIVES[part],
            lightgbm.Dataset(x, label=y, feature_name=FEATURES, free_raw_data=True),
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


def artifact(models, seasons):
    import lightgbm

    from fpl.models.feature_table import FEATURE_VERSION

    return {
        "version": MODEL_VERSION,
        "features": FEATURES,
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


def bootstrap(by_gameweek, statistic, draws=BOOTSTRAP, seed=SEED):
    """A 95% interval for `statistic` over gameweeks resampled with replacement."""
    rng = random.Random(seed)
    keys = sorted(by_gameweek)
    values = []
    for _ in range(draws):
        sample = [p for k in (rng.choice(keys) for _ in keys) for p in by_gameweek[k]]
        values.append(statistic(sample))
    values.sort()
    return values[int(0.025 * draws)], values[int(0.975 * draws) - 1]


def evaluate(bundle, rows):
    """Held-out figures for `bundle` on `rows`, with the naive comparator and intervals."""
    by_gw = defaultdict(list)
    for row, label in rows:
        points, _ = bundle.predict(row)
        by_gw[row["gameweek"]].append((points, naive(row), label["points"]))

    def mae(sample, i):
        return sum(abs(s[i] - s[2]) for s in sample) / len(sample)

    def rho(sample, i):
        return metrics.summary([(s[i], s[2]) for s in sample])["spearman"]

    every = [s for v in by_gw.values() for s in v]
    return {
        "n": len(every),
        "gameweeks": len(by_gw),
        "gbm": {
            "mae": mae(every, 0),
            "mae_ci": bootstrap(by_gw, lambda s: mae(s, 0)),
            "spearman": rho(every, 0),
            "spearman_ci": bootstrap(by_gw, lambda s: rho(s, 0)),
        },
        "naive": {"mae": mae(every, 1), "spearman": rho(every, 1)},
        "mae_gain": mae(every, 1) - mae(every, 0),
        "mae_gain_ci": bootstrap(by_gw, lambda s: mae(s, 1) - mae(s, 0)),
    }


def cross_validate(rows):
    folds = {}
    for season in CV_SEASONS:
        train = [r for r in rows if r[0]["season"] < season]
        test = [r for r in rows if r[0]["season"] == season]
        bundle = Bundle(artifact(fit(train), sorted({r[0]["season"] for r in train})))
        folds[season] = evaluate(bundle, test)
        print(
            f"{season}: MAE {folds[season]['gbm']['mae']:.3f} "
            f"(naive {folds[season]['naive']['mae']:.3f}), trained on {len(train)} rows"
        )
    return folds


def report(folds, live=None):
    def ci(pair):
        return f"{pair[0]:.3f} to {pair[1]:.3f}"

    lines = [
        f"# {MODEL_VERSION}: held-out report",
        "",
        "Rolling-origin cross-validation by season: each season predicted by a model trained "
        "on the seasons before it alone. 95% intervals from a bootstrap over gameweeks. "
        '"Naive" is recent points per 90 times recent minutes, on the same rows. Lower MAE '
        "is better; a gain above zero is gbm-v1 ahead.",
        "",
        "Not evidence of how gbm-v1 predicts: that is the log, from its first entry. "
        "This decides whether to log it (SPEC section 6).",
        "",
        "| Held out | Rows | MAE | MAE 95% | Naive MAE | Gain | Gain 95% "
        "| Spearman | Spearman 95% |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for season, f in list(folds.items()) + ([("2026/27 so far", live)] if live else []):
        g = f["gbm"]
        lines.append(
            f"| {season} | {f['n']} | {g['mae']:.3f} | {ci(g['mae_ci'])} | "
            f"{f['naive']['mae']:.3f} | {f['mae_gain']:+.3f} | {ci(f['mae_gain_ci'])} | "
            f"{g['spearman']:.3f} | {ci(g['spearman_ci'])} |"
        )
    return "\n".join(lines) + "\n"


def git_sha():
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def main(cv_only=False):
    from fpl.db.session import make_engine

    engine = make_engine()
    try:
        rows = training_rows(engine, SEASONS)
        live_season = max(SEASONS)
        live_season = f"{int(live_season[:4]) + 1}/{(int(live_season[:4]) + 2) % 100:02d}"
        live = training_rows(engine, [live_season])
    finally:
        engine.dispose()
    print(
        f"{len(rows)} training rows, {len(SEASONS)} seasons; {len(live)} rows of "
        f"{live_season} held out"
    )
    folds = cross_validate(rows)
    if cv_only:
        print(report(folds))
        return 0

    models = fit(rows)
    bundle_data = artifact(models, SEASONS)
    live_figures = evaluate(Bundle(bundle_data), live) if live else None
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(report(folds, live_figures))
    ARTIFACT.parent.mkdir(exist_ok=True)
    body = json.dumps(bundle_data, sort_keys=True).encode()
    ARTIFACT.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    registry = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
    registry[MODEL_VERSION] = {
        "sha256": digest,
        "url": URL,
        "release": RELEASE,
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
    print(f"\nwrote {ARTIFACT} (sha256 {digest}), {REPORT}, {REGISTRY}")
    print(f"publish: gh release create {RELEASE} {ARTIFACT}")
    return 0


def cli(argv=None):
    """`fpl-train-gbm`, or `python -m fpl.models.train_gbm`."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cv-only", action="store_true", help="the held-out report, no artifact")
    args = ap.parse_args(argv)
    require_project_root()
    return main(args.cv_only)


if __name__ == "__main__":
    sys.exit(cli())
