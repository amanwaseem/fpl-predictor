# 0022. A multi-gameweek plan, from predictions that are not logged

- **Status:** Accepted
- **Date:** 2026-10-27
- **Issues:** #55, #7

## Context

fpl-optimise (ADR 0010) and its transfers (#54) look one gameweek ahead. A
transfer that repays itself over three weeks but not this one is never made,
and a free transfer is always spent or rolled by this week's numbers alone.
Planning over several weeks needs predicted points for each of them, from one
deadline, and every logged model predicts only the next gameweek.

#55 left three questions open: whether those later predictions go in the log,
what the objective counts (free transfers rolling, a weight on later weeks,
chips), and which model predicts them.

## Decision

`fpl-plan` plans the next three gameweeks by default, and up to six, in one
integer programme: fpl-optimise's model once a week, joined by the transfers
between weeks.

- **Later weeks' predictions are not logged.** They are form-fixture-v1's
  rates from one snapshot, priced against each later fixture and counting its
  blanks and doubles. They are inputs to the plan, computed when it runs, and
  are neither written to `predictions/` nor scored. The log's claim is one
  gameweek ahead per entry; a horizon prediction would be a second kind of
  claim, with its own record to keep, for a feature that only needs a ranking.
- **form-fixture-v1 predicts them**, because its fixture adjustment is a
  function of the opponent and so prices any later fixture directly. gbm-v1's
  inputs are built per gameweek from settled rounds, and would need a
  separate feature build for weeks that have not been reached.
- **Free transfers roll over**, one a week, banked up to five, and each
  transfer beyond them costs four points in the week it is made.
- **Later weeks count less**: week k at 0.9^k of the first, hits included.
- **Not modelled:** chips, price changes (each player's price and selling
  price are as at the snapshot), and availability changing (a player injured
  now is injured for the whole plan).

Only the first week's transfers are a decision. The rest are the plan as it
stands, and the next snapshot revisits them.

## Consequences

- One-week plans agree with fpl-optimise, which the tests check, so the two
  cannot drift.
- A plan solves in a few seconds for three to six weeks on a real pool. It is
  a command, not an API route; the API's routes stay within their ten-second
  limit.
- Because availability is held fixed, the plan never banks on a return from
  injury. It can be slow to buy back a player who returns; the next week's
  snapshot corrects that.

## Revisit if

The main model can predict later weeks itself (gbm-v1 or a successor, from
features for future gameweeks), or chips are wanted in the plan: a wildcard
or free hit is one more binary per week in the same model.
