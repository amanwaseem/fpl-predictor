# 0003. The prediction log is append-only, and checked before it is written

- **Status:** Accepted
- **Date:** 2026-09-07
- **Issues:** #2, #26

## Context

Plenty of tools predict FPL points; almost none publish predictions in advance,
so their accuracy claims are measured on data the model was tuned against and
cannot be checked. This project's whole claim to credibility is the opposite:
predictions committed before each deadline and scored afterwards, including the
weeks they are wrong.

That only works if a committed prediction cannot be quietly improved. A log
that can be corrected after the fact is indistinguishable from one written with
hindsight.

## Decision

- Entries in `predictions/` are never modified or deleted once committed — not
  for bugs, not for embarrassment. A wrong prediction stays and is scored as
  wrong. This is hard rule 1 in CLAUDE.md.
- `tools/check_log_immutable.py` enforces it mechanically against the
  reference commit, in CI on every PR and every push to main.
- Because nothing can be fixed after the commit, everything that can be checked
  is checked before it:
  - `fpl/log.py:write_entry` validates every row against the declared schema
    (`FIELDS`, `validate_rows`), refuses an existing file, refuses to write
    into the log at or after the entry's own deadline, and writes atomically
    so a crash cannot leave a truncated entry at the final path.
  - `fpl-verify` cross-checks a written entry against the snapshot named in
    its own rows: coverage, descriptions, fixtures, availability, provenance.
- An entry covers every player in the snapshot, not just those the model
  rated, so the weeks it wrongly ignored someone can be scored too.
- The rule covers `predictions/` only. `scores/` and `reports/` are derived and
  are rewritten whenever a definition changes.

## Consequences

- The git history is the proof of when each prediction was made.
- A bug found after the merge is permanent in that entry. The remedy is a fix
  that applies to the next entry, and a note on the PR — not an amendment.
- A hand edit between writing and committing is caught only by `fpl-verify`;
  the immutability check passes any new file. The runbook says so.
- Validation is front-loaded into the deadline procedure, which is why the
  verifier counts all faults rather than stopping at the first.

## Revisit if

Never for the core rule — without it the project has no claim to make. The
mechanism could change: signed commits, or an external timestamp (an OpenTimestamps
proof, a third-party archive) if git history alone ever stops being accepted as
proof of timing.
