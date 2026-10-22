# 0010. Squad selection by integer programming, reusing the legal-XI selector

- **Status:** Accepted
- **Date:** 2026-09-06
- **Issues:** #7, #53

## Context

Given predicted points, the optimiser picks a 15-player squad: £100.0m budget;
2 GKP, 5 DEF, 5 MID, 3 FWD; at most 3 per club; a valid starting XI (1 GKP,
3–5 DEF, 2–5 MID, 1–3 FWD); a captain scoring double; and −4 points per
transfer beyond the free allowance. This recorded the approach decided for #7;
the first slice, without transfers, is built in #53 (`fpl/optimise.py`).

## Decision

- **Integer programming, not greedy selection.** Greedy is wrong here, not
  merely inelegant: the budget and per-club constraints interact, so a locally
  best pick can leave the remaining budget unspendable, and transfer costs make
  the best move this week frequently not the best over a horizon.
- **Built on the legal-XI selector's rules, not the reverse.** `fpl/xi.py`
  already picks the exact best legal XI (formation and club cap, no budget) for
  scoring's points-captured metric, verified against brute force. The optimiser
  cannot call `best_xi`, which has no budget, so it takes the rules instead:
  formations, the club cap, the point scale, the position check and the XI's
  order, all from `fpl/xi.py`. Its tests check its XI against `best_xi`.
  Scoring never depends on the optimiser, so a gameweek can always be scored
  without it.
- **The solver is HiGHS, called through `highspy`**, pinned in
  `requirements-optimise.txt`, apart from what a prediction needs (#53).
  - *Rejected:* scipy's `milp`. It wraps the same HiGHS, at about 25 MB with
    numpy against highspy's 9.5 MB.
  - *Rejected:* PuLP. Since 4.0 it bundles no solver and needs highspy or CBC
    installed beside it, so it would be a modelling layer over the same thing.
- **Squad, XI and captain are one model**, not chosen in turn, because the best
  squad depends on who would start. The formations and the club cap are
  derived from `fpl/xi.py`'s constants rather than restated, so the optimiser
  and the scoring harness cannot disagree about what is legal.
- **Transfers are the same model with more in it** (#54), not a second one:
  - a player already owned costs his selling price, and the money to spend is
    the bank plus what the squad sells for, which is FPL's rule written as one
    budget constraint;
  - an integer `hits` variable is at least the transfers beyond the free ones,
    and each costs 4 points;
  - the tie-break prefers fewer transfers before lower ids, so a transfer that
    only breaks even is never suggested.
  Selling prices are an input, because reading them needs the manager's login.
- **Exact and deterministic.** Points in hundredths and prices in tenths make
  every coefficient an integer, and the solver's gap is set to zero rather
  than left at its default. Ties go to lower player ids through a term too
  small to trade away a hundredth of a point.
- The optimiser reads committed entries and never writes to `predictions/`.

## Consequences

- The solution is checked against an exhaustive search on small random pools,
  and against `fpl/xi.py` on every committed entry.

- Selections are provably optimal for the stated objective, and every
  constraint is checkable in a test.
- A solver dependency, and a model formulation a reader has to understand.
- The objective is only as good as the predictions; an optimal squad on noisy
  points is still a noisy squad.

## Revisit if

- The solver is slow enough to matter. A full gameweek's pool of about 670
  players solves in a fraction of a second today; a multi-gameweek horizon
  (#55) multiplies the model's size.
- Multi-gameweek planning changes the formulation enough to need its own
  record.
