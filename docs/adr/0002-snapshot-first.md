# 0002. Snapshot first: never predict from the live API, and only from settled rounds

- **Status:** Accepted
- **Date:** 2026-09-07
- **Issues:** #13, #18

## Context

The only data source is the unofficial FPL API: undocumented, unversioned,
unsupported, and able to change shape without notice. It serves only the
present — there is no way to ask what it said last Tuesday. A prediction
made by calling it live could never be reproduced, and a schema change
would surface as a mysteriously wrong prediction rather than as a diff.

A second hazard is timing. While a gameweek is in progress the API returns a
history row for every player, including those whose fixture has not kicked off;
that row reads 0 minutes and 0 points, indistinguishable from being dropped.
Bonus points are provisional until the gameweek's `data_checked` flips.

## Decision

- `fpl-fetch` writes an immutable, timestamped copy of everything a prediction
  needs to `data/raw/<timestamp>/`. Models read snapshots, never the network.
- `manifest.json` is written last; a directory without it is an interrupted
  fetch and is refused, not half-used (`fpl/snapshot.py:load_snapshot`).
  `data/raw/LATEST` moves only after a manifest is written, so a failed fetch
  leaves the last good snapshot in place — the fallback the runbook depends on.
- Every entry row carries its `snapshot_id`, so any prediction can be rebuilt
  from the snapshot that produced it.
- A snapshot predicts its own next gameweek and nothing else
  (`resolve_target_gw`): earlier would use post-deadline availability, later
  would apply the wrong round's injury news.
- Features use only rounds whose results are final (`usable_rounds`, keyed on
  `data_checked`). Every run prints what it included and excluded.
- Snapshots are gitignored: large and regenerable while the API still serves
  the same season.

## Consequences

- Reproducibility is a claim the entries can back: the same snapshot and model
  produce a byte-identical file apart from `generated_at_utc`.
- A deadline run depends on a ~6-minute fetch succeeding, which is why the
  runbook decides the fallback (the last good snapshot) in advance.
- A snapshot not kept is gone for good: the API cannot regenerate the past.
  `fpl-verify` must run while the entry's snapshot is still on disk.
- Snapshots must be taken between gameweeks, not during one. A mid-gameweek
  snapshot loses the unsettled round rather than corrupting it, but it is
  still data thrown away.

## Revisit if

A stable, versioned or historical source becomes available (an official API,
or a third-party archive that can serve the state at a past deadline) — then
snapshotting becomes a cache rather than the only record, and the gitignore
question reopens. Or snapshot size makes keeping them impractical.
