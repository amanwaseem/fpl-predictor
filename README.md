# fpl-predictor

Gameweek-level points prediction and squad optimisation for Fantasy Premier League.

Every prediction is written to an append-only log **before** the gameweek deadline, then scored
against the actual result once the gameweek completes. The log is committed to this repository
and never rewritten, so the model's track record — including the weeks it gets things wrong — is
public and verifiable.

## Why

Most published FPL models report accuracy on a historical holdout set. That measures how well a
model fits the past, not how well it predicts the future, and it gives the author every
opportunity to tune against the test set until the numbers look good.

This project commits its predictions ahead of time instead. The claim is falsifiable and the
receipts are in the git history.

## Approach

Rather than regressing directly on total points, the system predicts each scoring component
separately and sums them:

| Component | Notes |
|---|---|
| Minutes | Gates everything else — a player who doesn't start scores nothing |
| Goals | Conditioned on expected minutes |
| Assists | Conditioned on expected minutes |
| Clean sheet | Team-level, shared across defenders and goalkeeper |
| Saves | Goalkeepers only |
| Defensive contribution | Tackles, interceptions, blocks, recoveries |
| Bonus | Derived from the Bonus Points System |

Components are individually interpretable and can be improved independently. A monolithic points
regressor gives you a single number and no way to tell which part of it is wrong.

Squad selection is then a constrained optimisation over the predicted points: maximise expected
return subject to the £100m budget, valid formation, the maximum of three players per club, and
the points cost of transfers.

## Current status

Early development, building against the 2026/27 season.

**Working**

- Raw data ingestion from the official FPL API, written to immutable timestamped snapshots
- Rolling-form baseline model, running end to end against a full snapshot
- Prediction log: a declared schema, validated before every write, with the append-only rule
  enforced mechanically rather than by discipline
- Pre-commit verification of an entry against the snapshot that produced it
- Human-readable digests of a committed entry

**In progress**

- Scoring harness

**Planned**

- Component models (minutes, goals, assists, clean sheet, saves, defensive contribution, bonus)
- Squad optimiser
- Public scorecard and web frontend

The rolling-form baseline exists so that the pipeline, optimiser and frontend can be built and
validated end to end without waiting on model accuracy. Any model that beats the baseline
replaces it; any model that doesn't, doesn't ship.

## Usage

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m fpl.fetch --skip-players   # bootstrap + fixtures only, a few seconds
python -m fpl.fetch                  # full snapshot including per-player history
```

Dependencies are pinned, transitive ones included. Every entry in the prediction log claims
reproducibility from its `snapshot_id`; that claim covers the data, and the pin covers the code
around it.

Predicting, and committing a prediction:

```bash
python -m fpl.predict_baseline --gw 4 --out scratch/   # exploratory, never the log
python -m fpl.predict_baseline --gw 4                  # writes predictions/gw04_baseline-v1.csv

python -m fpl.verify_entry predictions/gw04_baseline-v1.csv   # run before committing
python -m fpl.summarise predictions/gw04_baseline-v1.csv --out reports/
```

`predictions/` is the append-only log and is never rewritten. `scratch/` is gitignored space for
exploratory runs. `verify_entry` checks a written entry against the snapshot named in its own
rows and exits non-zero on any disagreement — it is the last check before an entry becomes
permanent, because after the commit nothing can be corrected.

The commands above are the middle of a longer procedure. **[`RUNBOOK.md`](RUNBOOK.md) is the
whole of it** — when to take the snapshot and why the window has a hard edge on both sides, what
to read in the output before trusting it, and what to do when the fetch fails near a deadline.
Follow it rather than the four commands here.

## Tests

```bash
python -m unittest discover tests
```

No third-party test runner. `python -m unittest discover -s tests -t .` and a bare
`python -m unittest` from the repository root work too, and all three run the same suite.

A snapshot also pulls `event/<gw>/live/` for every settled gameweek — the scoring harness's
source of actual points — fetched only once a gameweek's `data_checked` is true, because bonus
points are provisional until then. One request per gameweek rather than per player, so scoring
never needs a six-minute refetch to find out what happened. `manifest.json` records which
gameweeks a snapshot has live data for.

Snapshots are written to `data/raw/<timestamp>/` and are gitignored — they are large and fully
regenerable. `data/raw/LATEST` points at the most recent one.

## Data

Sourced from the official Fantasy Premier League API. This project is unaffiliated with the
Premier League or with Fantasy Premier League.

## Licence

MIT
