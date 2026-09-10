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

A snapshot holds `bootstrap.json`, `fixtures.json` and per-player history under
`players/`. `manifest.json` is written last and records what the snapshot
actually contains, so an interrupted fetch is visibly incomplete rather than
merely looking finished.

It will also hold `live/<gw>.json` — one file per completed gameweek, from
`event/{gw}/live/`, carrying the actual points the scoring harness joins
against, fetched only for gameweeks whose `data_checked` is true since bonus is
provisional until then. That **does not exist yet**; it lands with the scoring
harness in section 6.

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

| Column | Type | Description |
|---|---|---|
| `gameweek` | int | Target gameweek |
| `player_id` | int | FPL element id |
| `web_name` | str | Display name at time of prediction |
| `team` | str | Team short name |
| `position` | `GKP` / `DEF` / `MID` / `FWD` | |
| `price` | float, 1dp | Price in millions at time of prediction — `10.0`, not `100` |
| `predicted_points` | float, 2dp | The prediction |
| `expected_minutes` | float, 1dp | Intermediate output, retained for diagnosis |
| `points_per_90` | float, 2dp | Intermediate output, retained for diagnosis |
| `n_fixtures` | int | Fixtures in the target gameweek (0 = blank, 2 = double) |
| `status` | one of `a d i s u n` | Availability flag at prediction time |
| `model_version` | str, no `_` | Which model produced this |
| `snapshot_id` | str | Which data snapshot it was produced from |
| `generated_at_utc` | `YYYY-MM-DDTHH:MM:SSZ` | When |
| `deadline_utc` | `YYYY-MM-DDTHH:MM:SSZ` | The deadline it was committed ahead of |

The column set is declared in `fpl/log.py` as `FIELDS`, in this order, and
validated before every write. Types are part of the contract, not an
implementation detail: the scoring harness joins on `player_id` and does
arithmetic on `predicted_points`, and a column that changes units between
gameweeks silently corrupts the cumulative numbers.

An entry covers **all players in the snapshot**, not only those with
non-trivial expected minutes. A log that dropped the players the model thought
were irrelevant could not be scored for the weeks it was wrong about that.

`model_version` may not contain an underscore, or `gw04_baseline_v1.csv` has
two readings and the filename stops being parseable back into its parts.

### Ordering and determinism

Rows are sorted by descending `predicted_points`, ties broken by ascending
`player_id`. Fixing the order makes diffs between two model versions readable
rather than a reshuffle.

The same snapshot and the same model version produce a byte-identical file
apart from `generated_at_utc`. This is what makes `snapshot_id` a
reproducibility claim rather than a decoration.

### Rules

1. Files are written and committed **before** the gameweek deadline.
2. Files are **never** modified after commit. Not for bugs, not for
   embarrassment. Git history is the proof.
3. A new model version writes a new file; it does not replace an old one.
4. Intermediate outputs are retained so a wrong prediction can be attributed —
   bad minutes estimate versus bad scoring rate.
5. An entry whose `generated_at_utc` is at or after its `deadline_utc` is
   refused when the target is `predictions/`, and warned about elsewhere. A
   prediction made after the deadline cannot be distinguished from one made
   knowing the result, so it is not evidence of anything.

Rules 1 and 2 apply to `predictions/` only. `scores/` is derived output and is
explicitly regenerable — see section 6.

Every check that runs before the deadline is recoverable and every check that
runs after it is not, so validation happens at write time: `fpl/log.py`
rejects a malformed entry rather than letting the scoring harness discover it
weeks later, once the file is immutable. The write itself is atomic — an entry
is built alongside its target and linked into place — because a truncated CSV
at the final path would be refused by the append-only guard on the next run,
making recovery mean deleting a file out of `predictions/` in a hurry. `tools/check_log_immutable.py`
enforces rule 2 mechanically, comparing `predictions/` against the committed
tree and failing if an entry was modified or deleted.

`fpl/verify_entry.py` is the last check before an entry is committed. It reads
a written entry back and cross-checks it against the snapshot named in its own
`snapshot_id` column — row coverage against the element list, `n_fixtures`
against `fixtures.json`, flagged players against their availability, and
`deadline_utc` against the bootstrap event — then runs `validate_rows` over
the parsed rows. `fpl/log.py` cannot do this: it validates rows in memory at
write time with no snapshot open beside them. Run it before opening the PR:

```
python -m fpl.verify_entry predictions/gw04_baseline-v1.csv
```

## 6. Scoring

After a gameweek completes and `data_checked` is true in the API (bonus points
finalised), a scoring harness compares each logged prediction against actual
points and writes results to `scores/`. Actual points come from the snapshot's
`live/<gw>.json`. Scoring a gameweek whose `data_checked` is false is refused,
mirroring the same guard on the prediction side — bonus is provisional until
then, so the results file would be scoring numbers that later change.

### Joining predictions to actuals

A player in the log but absent from actuals — removed from the game,
transferred abroad — is **not** a zero. A player in actuals but absent from the
log — signed after the snapshot was taken — is not a model failure. Both go to
named buckets, are excluded from the metrics, and are reported with counts.
Scoring either as zero would quietly flatter or punish the model with nothing
in the output to reveal it.

### Metrics

- MAE and RMSE across all players
- The same, restricted to players with non-zero predicted minutes
- Spearman correlation of predicted versus actual ranking, with average-rank
  tie handling — hundreds of players tie at 0.0 predicted, so naive ranking is
  not merely imprecise, it is wrong
- Points captured by the best **legal XI** chosen on predicted points, versus
  the legal optimum chosen on actual points. Legal means a valid formation
  (1 GKP, 3–5 DEF, 2–5 MID, 1–3 FWD) and at most three players per club. There
  is no budget constraint on this metric, so it is exactly solvable and does
  not depend on the optimiser in section 7 — that optimiser reuses this
  selector, not the reverse
- MAE of `expected_minutes` against actual minutes, so a bad gameweek can be
  attributed to the minutes estimate or to the scoring rate
- Breakdown by position, so a failure points at the component responsible
- Comparison against the rolling-form baseline for the same gameweek

Reported per gameweek and cumulatively. Cumulative figures are **pooled across
all player-gameweek rows**, not a mean of per-gameweek means; with unequal
player counts the two differ, and only the pooled figure answers "how wrong has
this model been so far". Cumulative numbers only become meaningful after
roughly three gameweeks.

### `scores/` is regenerable

Unlike `predictions/`, score files are derived output. They can be recomputed
from the committed predictions plus a snapshot at any time, and are rewritten
freely when a metric definition changes. Score files stamp `harness_version`
and the actuals `snapshot_id` so that such a change is visible rather than a
silent rewrite of the track record.

The harness reads `predictions/` and writes nothing to it, ever.

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

The formation and per-club constraints are shared with the legal-XI metric in
section 6, which is built first because scoring needs it and it has no budget
term. The optimiser reuses that selector; the dependency does not run the other
way, so nothing here has to exist before a gameweek can be scored.

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
