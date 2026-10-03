# 0006. Only the log is evidence; held-out backtests decide what gets logged

- **Status:** Accepted
- **Date:** 2026-09-23
- **Issues:** #29, #39

## Context

Waiting a gameweek to learn whether a change helped is too slow to build
anything, so models are judged offline first by replaying settled gameweeks
(`fpl-backtest`). But a replay scores results that are already known, and a
model tuned on it has seen them. The first fixture-difficulty evidence (#31)
was exactly that: settings tuned and scored on the same four gameweeks. Re-run
held out (#39), most of its edge disappeared.

The two numbers are easy to blur later — a backtest table looks just like a
score table.

## Decision

- **Backtests choose what to log; only committed, pre-deadline entries are
  evidence.** The track record and the ship gate (0007) are judged on the log
  alone. Backtest output goes to `scratch/`, never `scores/` or `predictions/`.
- **Held out is the gate.** For each settled gameweek, re-tune over the
  model's declared grid on the other weeks and score the one left out. A factor
  stays only if it improves held-out results over the model without it.
  In-sample tables may appear alongside but decide nothing.
- **A setting that changes from fold to fold** is one the data cannot pin
  down, and the report shows it rather than averaging it away.
- **Leak-freedom is structural, not promised.** Models see a view with
  whitelisted player fields, history cut before the target, and the target's
  results stripped; `tests/test_backtest.py:TestNoLeak` poisons everything after
  the cut, for every model, and demands identical output.
- **Ideas are tested as candidates** (`fpl/candidates.py`) that change one
  thing against the baseline and are never logged. Once judged, the idea moves
  into a logged model and the candidate is deleted.

## Consequences

- Claims stay calibrated: SPEC states form-fixture-v1's held-out edge (1.117 vs
  1.138 MAE) as the reason it is logged, not as evidence that it is better.
- Four settled gameweeks resolve very little; most choices are made on thin
  margins, and the record says so.
- The backtest treats every player as available (availability is not known
  historically), so its absolute numbers are optimistic. Comparisons between
  models stay fair.

## Revisit if

The season ends and a full year of logged entries exists: then a backtest over
a previous season becomes possible, and its role in choosing models — though
never in evidencing them — could grow.
