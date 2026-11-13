# 0027. Score PRs are made, and merge themselves, once a gameweek settles

- **Status:** Accepted
- **Date:** 2026-11-13
- **Issues:** #162, #68, #119

## Context

Entries are made, opened and merged with nobody online (ADR 0024). Scoring
was still by hand (runbook section 10): someone had to notice the gameweek
had settled, take a snapshot, run `fpl-score`, edit both README results
tables until `tests/project/test_readme.py` passed, and open a PR. Nothing
breaks when that is late, so it was always the step that waited. Without it
the scorecard on the site and in the README stops at the last gameweek
someone scored, while entries keep arriving. That makes the record look
abandoned, when only the scoring step was.

Every judgement in a scoring PR is mechanical. `fpl-score` is deterministic.
Earlier gameweeks must not move. The README tables are a rendering of
`scores/`, and a test already checks them number by number.

## Decision

- **A score workflow** (`.github/workflows/score.yml`) runs every six hours.
  It acts when a logged gameweek has no score file, that gameweek is
  `finished` and `data_checked`, and the latest gameweek to start is settled
  too, so the snapshot is taken between gameweeks. Its decisions are in
  `tools/score_bot.py`, where they are tested.
- **The README tables are generated** by `tools/readme_results.py`. The test
  stays as the independent check.
- **Checks run before anything leaves the runner.** Only `scores/` and
  `README.md` may change. Every earlier gameweek must be unchanged apart from
  `actuals_snapshot_id`. Every new score file must have its `form`
  comparators: the new entries' snapshots are downloaded from the entry
  workflow's releases, and checked against the SHA-256 in the entry commits.
- **The actuals snapshot is released** as `snapshot-<id>`, like an entry's,
  so any score can be recomputed.
- **The PR merges itself once `ci` is green**, with `ENTRY_BOT_TOKEN`, as entry
  PRs do. Closing it is the veto.
- **There is no resume path.** A run that pushes and then fails leaves its
  branch, and the next run refuses to go on from it; a person scores by hand.
  Scoring has no deadline, so a person can always step in, and a resume path
  would cost more than it saves.

## Consequences

- The scorecard keeps up with the log without anyone online.
- A gameweek's scores appear within about six hours of `data_checked`,
  usually a day or two after its last match.
- A hand-made entry with no release has no snapshot the workflow can fetch.
  Its comparators would be missing, so the workflow refuses that gameweek and
  it is scored by hand.
- Retiring or promoting a model stays a reviewed decision (ADR 0020). The
  workflow only reports numbers.

## Revisit if

- FPL revises a settled gameweek's points. The unchanged-earlier-gameweeks
  check then fails every run until a person rescores by hand and says why.
