# 0023. Each season in its own log directory

- **Status:** Accepted
- **Date:** 2026-10-27
- **Issues:** #72, #71

## Context

Entry names carry the gameweek and not the season (`gw04_baseline-v1.csv`).
In 2027/28, GW4 wants the name 2026/27's GW4 already has. Since #71 the
watchdog and the entry workflow notice the clash and stop, but nothing could
make the 2027/28 entry. The options, weighed in #72 on 21 October 2026:

- **C1:** 2026/27 stays in `predictions/`; each later season gets
  `predictions-<season>/`.
- **C2:** move 2026/27 into `predictions-2026-27/` too, for one naming scheme.
- **B:** put the season in the filename (`2027-28_gw04_baseline-v1.csv`).
- **D:** do nothing until the summer.

## Decision

C1. `predictions/` is 2026/27's log for good, and from 2027/28 each season's
entries go in `predictions-<season>/`, e.g. `predictions-2027-28/`, with
filenames unchanged inside. Derived output follows the same pattern:
`scores-2027-28/` and `reports-2027-28/`.

- **One mapping, in the log's contract.** `fpl.log.contract` names a
  season's directory and reads a directory's season back
  (`season_directory`, `season_of_directory`), and every reader and writer
  uses it: the predictors and `fpl-entry`, `fpl-verify`, `fpl-score`, the
  database projection, the deadline watchdog and the entry workflow.
  `tools/check_log_immutable.py` runs without the package, so it keeps its
  own pattern; a test checks it covers every directory the contract names.
- **The season comes from the entry, not the folder.** An entry's season is
  read from its own `deadline_utc` (July to June). `write_entry` refuses an
  entry for another season's directory, and `fpl-verify`, `fpl-score` and the
  projection refuse an entry found in the wrong one.
- *Rejected:* C2. Moving a committed entry is rewriting it, which hard rule 1
  forbids and `check_log_immutable.py` would rightly fail; it would need an
  exception to the one rule the log's credibility rests on.
- *Rejected:* B. Two naming schemes in one folder, and every filename parser
  would need to know both.
- *Rejected:* D. The mapping is small now, and a summer deadline is when
  there is least time to check it.

## Consequences

- Nothing about 2026/27 changes: its files and paths stay as they are, and
  the tests check they are byte-identical with a 2027/28 season beside them.
- `predictions/` reads as the plain name only because it came first; the
  README, SPEC and RUNBOOK say so.

## Revisit if

A season needs entries that are not gameweeks (a cup, a second game mode),
which would want a directory scheme of its own rather than a season suffix.
