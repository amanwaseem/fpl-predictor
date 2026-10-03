# 0009. Model fixture difficulty explicitly, with our own xG ratings

- **Status:** Accepted
- **Date:** 2026-09-23
- **Issues:** #31, #39

## Context

SPEC left open whether to model fixture difficulty explicitly or let
opponent-conditioned components absorb it. GW4's largest errors were whole
clubs, which is what a fixture-blind model produces.

The first evidence for an explicit model (#31) was in-sample: settings tuned
and scored on the same gameweeks. Re-run held out (#39) — tuned on three of
GW2–5, scored on the fourth — it was weaker:

| held out, GW2–5 | MAE | club sd |
|---|---|---|
| baseline-v1 | 1.138 | 16.9 |
| xG team ratings alone | 1.125 | 16.8 |
| FPL difficulty alone | 1.125 | 16.7 |
| prior + FPL difficulty | 1.119 | 16.5 |
| **prior + xG team ratings** | **1.117** | **16.4** |

Alone, the xG ratings only tie FPL's difficulty. With the player prior (#30)
they lead on every measure, by 0.002 MAE and 0.1 club sd — within what four
gameweeks can resolve.

## Decision

Model fixture difficulty explicitly, in one shared module, `fpl/teams.py`. Each
club gets attack and defence ratings from settled rounds' xG, fitted jointly
and shrunk toward league average. A fixture becomes expected goals for each
side and a Poisson clean-sheet probability. Models read fixtures through this
module, never on their own.

The decision rests on these grounds, not on the accuracy edge:

- the ratings describe each club, not just each fixture;
- they are built only from data available at the deadline, while a snapshot's
  FPL difficulty is the current value, not the one published at the time;
- they give the expected goals that a clean-sheet probability needs, which a
  1–5 scale cannot;
- opponent-conditioned components (0008) have nothing to condition on without
  them, so "let the components absorb it" needs this module anyway.

Rejected: FPL's `team_h/a_difficulty` (coarse, not as-published, and no better
held out); a home-advantage term (made every setting worse in the backtest).

## Consequences

- One place owns team strength, shared by every model.
- The claim is modest, and stays modest: no measured accuracy advantage over
  the simpler option yet.

## Revisit if

Re-running `fpl-backtest --held-out` for `form-fixture-v1` (which is prior +
xG ratings, the row in bold above) and `baseline-prior-fdr` as gameweeks settle
shows FPL difficulty matching or beating the ratings over a larger sample —
then the simpler option wins. Or a season of logged entries shows
fixture-aware models losing to `baseline-v1`, or a new source (bookmaker odds)
earns its dependency.
