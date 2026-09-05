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

**In progress**

- Rolling-form baseline model
- Prediction log format and scoring harness

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
pip install requests

python fetch_fpl.py --skip-players   # bootstrap + fixtures only, a few seconds
python fetch_fpl.py                  # full snapshot including per-player history
```

Snapshots are written to `data/raw/<timestamp>/` and are gitignored — they are large and fully
regenerable. `data/raw/LATEST` points at the most recent one.

## Data

Sourced from the official Fantasy Premier League API. This project is unaffiliated with the
Premier League or with Fantasy Premier League.

## Licence

MIT
