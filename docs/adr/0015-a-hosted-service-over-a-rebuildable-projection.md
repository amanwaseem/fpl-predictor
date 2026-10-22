# 0015. A hosted service over a rebuildable projection

- **Status:** Accepted
- **Date:** 2026-10-22
- **Issues:** #75, #80, #81, #82, #83
- **Supersedes:** [0014](0014-a-static-json-api-not-a-hosted-server.md)

## Context

ADR 0014 chose static JSON on GitHub Pages because what it would serve
changes weekly, is the same for every viewer and takes no input. It named
when to revisit: "A feature needs user input, like 'optimise my team'."

That feature is now planned (#83): a manager enters their FPL team ID and gets
transfers, an XI and a captain. The answer depends on their squad, so it
cannot be precomputed into files. Several other things have also changed:

- The data is in Postgres (#77, #78), queryable across gameweeks and players
  in ways a directory of JSON files is not.
- The log can be rebuilt into the database and checked against git row by row
  (`fpl-project`, `fpl-reconcile`, #80).
- A read API over that database exists, with a committed OpenAPI contract that
  CI fuzzes (#81).

0014's main concern still stands. The log is the credibility mechanism (0003,
0006), and a server with its own store is a second copy of the record that can
drift from the first. Any hosted design has to answer that.

## Decision

- **A hosted API**: FastAPI on Fly.io, over Postgres on Neon, deployed by
  `.github/workflows/deploy.yml` after CI passes on `main`.
- **The database is a projection, never a second record.**
  - Every deploy rebuilds the log tables from the committed `predictions/` and
    `scores/`, then runs `fpl-reconcile`, which fails the deploy on any
    difference.
  - Nothing writes to those tables except `fpl-project`.
  - The API is read-only over them. #83 adds computation (the optimiser), not
    storage of the record.
- **Migrations run before traffic.** Fly runs `fpl-migrate` as the release
  command, in a one-off machine from the new image. A failed migration stops
  the deploy, and the old version keeps serving.
- **What the image contains.** It installs the package rather than an
  editable checkout, which is the one exception to 0011. The image runs only
  `fpl-serve` and `fpl-migrate`, which read nothing but the database, so the
  reason for 0011 (a stale copy of the code making a log entry) does not
  arise. Entries are still made from a checkout.
- **The contract is still versioned by path.** Routes live under `/v1/`, and a
  breaking change is `/v2/`. `docs/openapi.json` is committed, so any change
  to the contract is visible in review.
- *Rejected:* keeping 0014 and adding a separate service only for #83. That
  would mean two ways of serving the same data, and the static files would
  still need a build step that the database replaces.

## Consequences

- There is something to keep running: a Fly app and a Neon database.
  - Both idle down. The Fly machine stops when unused and starts on the next
    request; Neon's free tier suspends an idle database.
  - The first request after a quiet spell is slower, about a second or two
    while each wakes.
- **The cost is small but not zero.** Fly needs a card on file and charges for
  machine time; Neon's free tier covers this project's size.
- Secrets exist now: a Fly deploy token and the database URL. They live in the
  `production` GitHub environment and in Fly's secrets, never in the
  repository.
- The record can still be checked independently. The API serves what git holds
  at the deployed commit, and anyone can run `fpl-project` and
  `fpl-reconcile` against their own database to confirm it.
- **Football data is not in git** (snapshots are not committed, 0002). It
  reaches production by running `fpl-load` against it from a machine with a
  snapshot, until ingestion is scheduled. Until then, the scorecard's missed
  gameweeks and the player pages show only what was last loaded.

## Revisit if

- The service needs to store something users provide, such as a saved squad.
  That is a second kind of data with its own retention and privacy questions,
  and needs its own record.
- Hosting cost or upkeep outgrows what the project is worth. The static build
  from 0014 remains possible from the same tables.
- Cold starts make the first request too slow for the frontend (#10). Keeping
  one machine running is a single setting in `fly.toml`.
