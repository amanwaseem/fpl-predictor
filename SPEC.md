# fpl-predictor — Specification

Status: draft, 2026/27 season.

## 1. Problem

Fantasy Premier League managers pick 15 players under a budget cap and must
decide, each gameweek, who to start and who to captain. The decision is a
prediction problem — how many points will each player score — followed by a
constrained optimisation.

Plenty of tools predict FPL points. Almost none of them publish their
predictions in advance, which means their accuracy claims are unverifiable and
usually measured on data the model was tuned against.

## 2. Goal

Predict per-player points for each gameweek, optimise squad selection under FPL's
rules, and maintain a public append-only log that makes the model's real-world
accuracy verifiable.

### Non-goals

- Beating the global FPL rank leaderboard. The measurable target is prediction
  accuracy against a stated baseline, not rank.
- Real-time in-play updates.
- Supporting historical seasons before 2026/27.

## 3. Data

Source: the unofficial FPL API. No authentication, no documentation, no
stability guarantees.

Ingestion is **snapshot-first**: `fpl/fetch.py` writes an immutable timestamped
copy of the raw API response to `data/raw/<timestamp>/`. Every prediction records
the `snapshot_id` it was produced from, so any prediction can be reproduced
exactly, and schema changes can be diagnosed by diffing snapshots.

Raw snapshots are gitignored — large and fully regenerable.

## 4. Prediction approach

Rather than regressing on total points, each scoring component is predicted
separately and summed.

| Component | Applies to | Notes |
|---|---|---|
| Minutes | All | Gates everything else |
| Goals | All | Conditioned on expected minutes |
| Assists | All | Conditioned on expected minutes |
| Clean sheet | GKP, DEF, MID | Team-level, shared across the defence |
| Saves | GKP | |
| Defensive contribution | DEF, MID | Tackles, interceptions, blocks, recoveries |
| Bonus | All | Derived from BPS |

**Rationale.** A single points regressor gives one number and no way to tell
which part of it is wrong. Component decomposition means each piece can be
evaluated and improved independently, and the failure modes are legible — if
predictions are bad for defenders specifically, the clean sheet model is the
first suspect.

Components are intended to conform to a common `ComponentModel` interface so the
pipeline can run with any mix of implementations. That interface **does not exist
yet** — it gets defined alongside the first component models, after the break.

### Baseline

A rolling-form model (`fpl/predict_baseline.py`) covers the same ground using no
machine learning: recent minutes and points-per-90, shrunk toward a positional
prior, scaled by availability and fixture count. It is a standalone script rather
than a `ComponentModel` implementation, and gets retrofitted once that interface
exists.

The baseline exists so the full pipeline — optimiser, API, frontend — can be
built and validated end to end without waiting on model accuracy. It is also the
benchmark: **any component model that does not beat the baseline does not ship.**

### Input requirements

Features are built only from gameweeks whose results are final — `data_checked`
true in the API — matching what section 6 already requires of scoring. This is
settled, not a preference.

A snapshot taken mid-gameweek carries a history row for every player, including
those whose fixture has not kicked off. That row reads 0 minutes and 0 points
and is indistinguishable from a genuine non-appearance, so an unplayed match
would otherwise be scored as a benching, biasing predictions toward whichever
teams happened to have played before the snapshot was taken. It is not a
leakage problem — the rounds are strictly before the target — but it corrupts
the features just as effectively.

Every run prints which rounds were included and excluded. If no round before
the target has final results, the run writes nothing and exits non-zero.

## 5. Prediction log

The core artifact. One CSV per gameweek per model version, in `predictions/`,
named `gw<NN>_<model_version>.csv`.

| Column | Description |
|---|---|
| `gameweek` | Target gameweek |
| `player_id` | FPL element id |
| `web_name` | Display name at time of prediction |
| `team` | Team short name |
| `position` | GKP / DEF / MID / FWD |
| `price` | Price in millions at time of prediction |
| `predicted_points` | The prediction |
| `expected_minutes` | Intermediate output, retained for diagnosis |
| `points_per_90` | Intermediate output, retained for diagnosis |
| `n_fixtures` | Fixtures in the target gameweek (0 = blank, 2 = double) |
| `status` | Availability flag at prediction time |
| `model_version` | Which model produced this |
| `snapshot_id` | Which data snapshot it was produced from |
| `generated_at_utc` | When |
| `deadline_utc` | The deadline it was committed ahead of |

### Rules

1. Files are written and committed **before** the gameweek deadline.
2. Files are **never** modified after commit. Not for bugs, not for
   embarrassment. Git history is the proof.
3. A new model version writes a new file; it does not replace an old one.
4. Intermediate outputs are retained so a wrong prediction can be attributed —
   bad minutes estimate versus bad scoring rate.

## 6. Scoring

After a gameweek completes and `data_checked` is true in the API (bonus points
finalised), a scoring harness compares each logged prediction against actual
points and writes results to `scores/`.

Metrics:

- MAE and RMSE across all players
- The same, restricted to players with non-zero predicted minutes
- Spearman correlation of predicted versus actual ranking
- Points captured by the top-N predicted players versus optimal
- Comparison against the rolling-form baseline for the same gameweek

Reported per gameweek and cumulatively. Cumulative numbers only become
meaningful after roughly three gameweeks.

## 7. Optimiser

Given predicted points, select a squad maximising expected return subject to:

- Budget: £100.0m
- 15 players: 2 GKP, 5 DEF, 5 MID, 3 FWD
- Maximum 3 players per club
- Valid starting XI formation (1 GKP, 3–5 DEF, 2–5 MID, 1–3 FWD)
- Captain: 2× multiplier
- Transfer cost: −4 points per transfer beyond the free allowance

Formulated as constrained integer programming. Greedy selection is not
sufficient — the budget and per-club constraints interact, and transfer costs
make the locally optimal move frequently wrong.

## 8. Delivery

- **API** — prediction endpoints over the current gameweek
- **Frontend** — squad suggestions plus the public scorecard
- **CI** — tests and lint on every PR

## 9. Milestones

| Milestone | Target |
|---|---|
| Data ingestion working | Done |
| Rolling-form baseline | Before GW4 |
| Prediction log schema fixed | Before GW4 |
| GW4 predictions committed | 2026-09-12 |
| GW5 predictions committed | 2026-09-18 |
| Scoring harness | International break |
| Package structure, CI, ADRs | International break |
| Optimiser | International break |
| Component models | From GW6 |
| API and frontend | From GW6 |

## 10. Open questions

- How to handle players transferred between clubs mid-season — `player_id` is
  stable but team affiliation is not.
- Whether to model fixture difficulty explicitly or let opponent-conditioned
  components absorb it.
- Whether the log should record predictions for all ~650 players or only those
  with non-trivial expected minutes. Currently all, for completeness.
