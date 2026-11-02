# 0024. Entry PRs merge themselves once ci is green

- **Status:** Accepted
- **Date:** 2026-11-02
- **Issues:** #119, #118, #117, #68

## Context

The entry workflow (#68) opened GW9's PR ten hours before the deadline, from
a good snapshot, with every entry verified. It was still missed. `ci` went
red on two tests that read whatever `predictions/` holds (#118), nobody was
there to see it, and the deadline passed with the PR open. The watchdog sent
one email: GitHub ran its hourly schedule six times that day.

The human merge was the last manual step between a correct entry and the log,
and it is the one that failed. Everything a person was asked to check before
merging is already checked by machine. `fpl-entry` prints the included and
excluded rounds and refuses unsettled ones. `fpl-verify` checks the entry
against its snapshot and its deadline. `ci` gates the rest.

## Decision

- **The entry workflow turns on auto-merge** (rebase) when it opens its PR,
  with `ENTRY_BOT_TOKEN`, so the merge triggers the deploy like any other.
  The PR merges itself the moment `ci` is green.
- **`ci` refuses a late entry.** On a PR, `tools/check_log_immutable.py
  --new-before-deadline 30` fails when a new entry's own `deadline_utc` is 30
  minutes away or less, or past. A green `ci` therefore means the entry is
  on time. A re-run or a slow queue after the deadline stays red and cannot
  merge a late entry. The 30 minutes covers the rest of the run.
- **The late-entry guard turns auto-merge off** on any open PR for a past
  deadline before it comments.
- **Closing the PR is the veto.** A person can still read it. If something is
  wrong, closing it before `ci` goes green stops the merge, and the gameweek
  goes back to the hand runbook.

## Consequences

- An entry reaches the log without anyone online. That was already true of
  making it; now it is true of committing it.
- A red `ci` still blocks the entry, so tests must not depend on the newest
  log files (#118). A flaky test on an entry PR is a missed gameweek.
- Hand-made entries get the same guard: a PR opened in the last half hour
  cannot go green. The runbook's own margin is hours, not minutes.

## Revisit if

- An automated entry merges that a person would have refused. Then the
  checks that person made belong in `fpl-verify`, not back in a manual step.
