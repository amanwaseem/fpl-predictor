# 0004. A missed deadline stays missed

- **Status:** Accepted
- **Date:** 2026-09-22
- **Issues:** #3, #11, #33

## Context

GW5's deadline (2026-09-18T17:30:00Z) passed while no one was working on the
project, and no entry was committed. An entry for it could still be produced
afterwards, from a snapshot taken before that deadline, and would look exactly
like one made on time.

## Decision

- No late entries and no backfill. A prediction made after its deadline cannot
  be told apart from one made knowing the result, so it is not evidence.
  `fpl/log.py` refuses to write one into `predictions/` (SPEC §5 rule 5), and
  hard rule 2 in CLAUDE.md forbids using post-deadline information.
- The gap is shown, not skipped. After a model's first entry, a settled
  gameweek with no entry is listed as missed in `scores/cumulative.json`
  (GW5 for `baseline-v1` today), so the record never reads as an unbroken run.
- To shrink the chance of a second gap, the runbook sets the fallback in
  advance: if the fresh fetch fails, commit from the last good snapshot.

## Consequences

- The log has a permanent hole at GW5, and baseline-v1's cumulative numbers
  rest on one fewer gameweek.
- Cumulative figures are honest about coverage: a reader sees what was missed
  without having to look for it.
- Missing a deadline has a real cost, which is what keeps the deadline taken
  seriously: GW6's plan aims to merge 16 hours early.

## Revisit if

Never for the rule. The visibility could improve — the public scorecard (#10)
should show missed gameweeks as plainly as scored ones.
