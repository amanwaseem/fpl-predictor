# 0005. One entry per gameweek per model, and a model is fixed from its first entry

- **Status:** Accepted
- **Date:** 2026-09-10
- **Issues:** #4, #8, #33

## Context

Twice the question has come up of logging something extra.

Before GW4 (2026-09-10) the plan was two entries: one from an early snapshot as
insurance, and a fresh one on the Friday. And before GW6 (2026-10-02), with
`form-fixture-v1` not yet logged, the question was whether #8's new components
should become a third model version or go into `form-fixture-v1`.

`model_version` is the key the scoring harness groups and pools on. Each value
is a track record.

## Decision

- **One entry per (gameweek, model version).** GW4 got one entry; the second
  was cancelled. A second file for the same model cannot replace the first
  (0003), it can only sit beside it — and a one-off variant becomes a phantom
  model with a single-gameweek record that can never be fairly compared.
- **To measure a pipeline variable** — a fresher snapshot, a parameter — run it
  into `scratch/` and diff against the committed entry, and post the diff on
  the PR. Or run both arms every week, so both build real records.
- **A model may change freely until its first entry, and not after.** Once
  logged, any change to its predictions is a new `model_version`. So #8's
  components go into `form-fixture-v1` before its first entry at GW6, and GW6
  stays a clean two-model comparison.
- Every model logged for a gameweek is run from the same snapshot, so a
  head-to-head compares models, not snapshots.

## Consequences

- Each model version's record is long, comparable, and means one thing.
- Changing `MODEL_VERSION` is a log-visible decision, not a refactor; and a
  refactor that changes output silently is a bug, which is why changes are
  checked for byte-identical output.
- Improvements to a logged model wait for a new version, which starts its
  record from zero. That is a real cost, paid deliberately.
- GW4 has less insurance against a stale snapshot; the measured churn between
  the two candidate snapshots (5 of 653 players' availability, none in the top
  100) said that was cheap.

## Revisit if

The log grows enough versions that a family structure (for example
`form-fixture-v1` → `-v2`, scored both separately and as a lineage) would read
better than independent records.
