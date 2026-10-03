# 0010. Squad selection by integer programming, reusing the legal-XI selector

- **Status:** Proposed
- **Date:** 2026-09-06
- **Issues:** #7

## Context

Given predicted points, the optimiser picks a 15-player squad: £100.0m budget;
2 GKP, 5 DEF, 5 MID, 3 FWD; at most 3 per club; a valid starting XI (1 GKP,
3–5 DEF, 2–5 MID, 1–3 FWD); a captain scoring double; and −4 points per
transfer beyond the free allowance. Not built yet — this records the approach
decided for #7.

## Decision

- **Integer programming, not greedy selection.** Greedy is wrong here, not
  merely inelegant: the budget and per-club constraints interact, so a locally
  best pick can leave the remaining budget unspendable, and transfer costs make
  the best move this week frequently not the best over a horizon.
- **Built on the legal-XI selector, not the reverse.** `fpl/xi.py:best_xi`
  already picks the exact best legal XI (formation and club cap, no budget) for
  scoring's points-captured metric, and is verified against brute force. The
  optimiser reuses it; scoring never depends on the optimiser, so a gameweek can
  always be scored without it.
- The solver is the project's first dependency beyond `requests`, so it is
  pinned deliberately and chosen in #7.
- The optimiser reads committed entries and never writes to `predictions/`.

## Consequences

- Selections are provably optimal for the stated objective, and every
  constraint is checkable in a test.
- A solver dependency, and a model formulation a reader has to understand.
- The objective is only as good as the predictions; an optimal squad on noisy
  points is still a noisy squad.

## Revisit if

Status moves to Accepted when #7 lands. Revisit if the chosen solver is slow
enough to matter at a deadline, or if multi-gameweek planning (a transfer
horizon) changes the formulation enough to need its own record.
