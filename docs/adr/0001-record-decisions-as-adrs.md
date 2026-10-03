# 0001. Record decisions as ADRs

- **Status:** Accepted
- **Date:** 2026-10-03
- **Issues:** #11

## Context

Several of this project's load-bearing decisions look like overengineering from
outside: a prediction log that can never be corrected, snapshotting an API that
could simply be called, commands that refuse to run outside the repository
root. The reasoning that makes each one correct lives in SPEC prose, in issue
comments and in the shape of the code — none of which says why, or what would
change the answer. A reader evaluating the project, or a future contributor
tempted to "simplify", cannot recover it.

## Decision

Each such decision gets a short record in `docs/adr/`, numbered in order and
never renumbered: `NNNN-kebab-title.md`, with a header (Status, Date, Issues)
and four sections — Context, Decision, Consequences, Revisit if.

- **Status** is one of: `Accepted`; `Proposed` — decided, not yet built;
  `Open` — recorded so that it is not decided by accident; or
  `Superseded by NNNN`.
- **Revisit if** is required. A decision with no stated condition for
  reopening it reads as dogma.
- Records are short and cite code and issues rather than repeating them.
- An accepted record is not rewritten. A changed decision gets a new record
  that supersedes the old one, and the old one's status says so. This mirrors
  the prediction log, but only as a convention: an ADR is reasoning, not
  evidence, and nothing enforces it.
- `docs/adr/README.md` indexes every record with its status.

Minor choices do not get records. The test is whether someone would plausibly
undo the choice without knowing the reason for it.

## Consequences

- The reasoning lives next to the code and moves with it; issue comments stay
  as the discussion that led there.
- `tests/test_adr.py` keeps the numbering contiguous, every status valid, and
  the index complete, so the set cannot quietly rot.
- SPEC still describes *what* the system does. Where it gave reasons, it now
  points here.

## Revisit if

Records stop being written for decisions that clearly needed one, or the set
grows past what a reader would go through in one sitting — at that point an
index grouped by topic, or pruning superseded records into an appendix, is
worth more than strict chronology.
