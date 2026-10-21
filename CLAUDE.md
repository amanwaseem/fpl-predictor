# CLAUDE.md

Context for agentic work in this repository. `SPEC.md` has the requirements,
open issues have the plan, `RUNBOOK.md` has the steps for a prediction entry,
and `docs/adr/` has the reasoning behind past decisions.

Everything here is a default, not a rule. If something in this file, the spec
or an ADR gets in the way of the work, change it as part of that work and say
what changed and why.

## What this is

An FPL points predictor with a public prediction log, being built out into a
hosted product: data in Postgres, a FastAPI service, a squad optimiser and a
web frontend.

The log is what makes the predictions credible: entries in `predictions/` are
committed before each gameweek's deadline and scored afterwards. That only
works if the following hold:

- Entries already in `predictions/` stay as they were committed. CI
  (`tools/check_log_immutable.py`) checks this. `scores/` and `reports/` are
  derived and can be regenerated freely.
- A gameweek's features come only from data available before its deadline.
- Raw snapshots stay out of git (`data/raw/` is gitignored). They are large and
  can be regenerated with `fpl-fetch`.

## Working norms

- Work on a branch per issue, off `main`, and open a PR.
- During a gameweek week, the next entry comes first.
- Run the `fpl-*` commands from the repository root with `.venv` active.

## Database

Postgres runs locally in Docker: `make up migrate seed` starts it, applies the
migrations and loads `data/raw/LATEST`. Commands read `DATABASE_URL`. The
database tests (`tests/test_db*.py`) need `TEST_DATABASE_URL` and skip without
it. `make test-db` sets it. After changing `fpl/db/models.py`, generate a
migration with `alembic revision --autogenerate` and read it before
committing.

## Data source

The unofficial FPL API at `https://fantasy.premierleague.com/api/`. It is
undocumented and can change without notice, which is why ingestion works from
snapshots. Take snapshots between gameweeks: mid-gameweek ones contain
zeroed rows for fixtures that haven't kicked off yet, and bonus points are
provisional until `data_checked` is true.

## Conventions

- Python 3.13. Add dependencies when they're useful, and pin them.
- Timestamps are in UTC. Gameweek filenames are zero-padded (`gw04`).
- `scratch/` is gitignored and holds exploratory runs.
- Prefer explicit, readable code over clever code.
