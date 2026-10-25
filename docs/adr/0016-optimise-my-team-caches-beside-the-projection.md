# 0016. Optimise my team: caches beside the projection, estimates said aloud

- **Status:** Accepted
- **Date:** 2026-10-25
- **Issues:** #83, #75

## Context

0015 made the database a projection of the log and the API read-only over
it, and said #83 would add computation, not storage of the record. #83 adds
two routes that run the solver: `POST /v1/optimise`, for a squad sent in the
body, and `GET /v1/entries/{id}/advice`, which reads a manager's squad from the
FPL API first. Three things follow that 0015 did not settle:

- A solve takes a tenth of a second or more (three solves for a transfer plan),
  where every other route is a query. Repeats are common: the same squad asked
  about twice, or many managers on the same template squad near a deadline.
- The advice route calls an API that is undocumented and rate-limits.
- The public FPL endpoints give a manager's fifteen players, bank and
  transfer history, but not two things the plan depends on: selling prices
  and free transfers. Both need the manager's login, which this service will
  not ask for.

## Decision

- **Two cache tables, and nothing else written by the API.**
  `optimise_results` keeps each result under a hash of everything that
  decides it; `upstream_responses` keeps FPL API responses for ten minutes.
  Neither is the record, and both can be emptied at any time.
- **A result cannot outlive what it was solved from.** Each row has a foreign
  key to its prediction entry with `ON DELETE CASCADE`. The projection
  replaces every entry on each deploy, so each deploy also empties the
  results: a cached answer is never from older code or older predictions.
- **The solver is bounded.** All of one request's solves share a time limit
  (`FPL_SOLVER_TIME_LIMIT`, ten seconds); running out is a 503 with
  `Retry-After`, never an answer not proved optimal. The two routes are
  rate-limited per client, in memory per machine (`FPL_SOLVER_RATE`, ten a
  minute).
- **Estimates are reconstructed and labelled.** Selling price is FPL's rule
  (half of any rise, rounded down) applied to the purchase price from the
  manager's transfers, or to the price in their first gameweek, from the
  loaded stats. Free transfers are counted from the history: one a week,
  banked up to five, none used by a wildcard or free hit. The response's
  `estimates` says so, and `free_transfers` can be given instead. The squad
  planned from is the one before a free hit, which comes back at the next
  deadline.
- *Rejected:* asking for the manager's FPL login to read exact selling
  prices. A service that holds other people's credentials needs much more
  than this one has, for a difference of £0.1m here and there.
- *Rejected:* an in-memory results cache. Fly stops idle machines, so it
  would be empty for most first requests; Postgres is already there.

## Consequences

- The API now writes, but only to tables nothing reads as evidence.
  `fpl-reconcile` checks the log tables, not these.
- Advice on a squad whose selling prices the estimate gets wrong can be off
  by the difference: a transfer that is affordable in the plan may be £0.1m
  short in the game. The plan says how the prices were estimated.
- A second machine doubles the effective rate limit per client. Acceptable
  while the limit is there to stop one client monopolising the solver, not to
  meter anyone.
- The CI fuzz cannot expect a random squad to be accepted, so
  `schemathesis.toml` counts a 422 as acceptance for `/v1/optimise`, and the
  advice route, which would call the real FPL API, is not fuzzed from CI.

## Revisit if

- FPL publishes selling prices or free transfers without a login, or the
  free transfer rules change for a season (2025/26's AFCON top-up was one).
- The results table grows past what Neon's free tier holds between deploys.
- A multi-gameweek horizon (#55) makes solves slow enough that the time limit
  needs raising, or the work needs a queue rather than a request.
