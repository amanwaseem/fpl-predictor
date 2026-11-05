# Development

Everything needed to work on the code: setup, the commands, the tests, the
database, the API and the web frontend. Making a real log entry is a separate,
stricter procedure, in [`runbook.md`](runbook.md).

## Setup

With [uv](https://docs.astral.sh/uv/) and Python 3.13:

```bash
uv sync --locked && source .venv/bin/activate            # development: everything, with the tests
uv sync --locked --no-dev && source .venv/bin/activate   # making an entry: requests alone
```

An entry is made in the second, the environment the entry workflow uses, so a
prediction that imported anything beyond `requests` would fail there rather
than work by accident. The other commands need more, in dependency groups in
`pyproject.toml`: `--group db` for the database commands, `--group optimise`
for `fpl-optimise` (the HiGHS solver), `--group api` for `fpl-serve`, which
runs the solver too, and `--group ml` for training. The default, `dev`, is all
of them with the test tools. `uv sync` is exact, so name every group wanted:
`--no-dev --group db --group optimise`.

Every version, transitive ones included, is in `uv.lock`, and `--locked`
installs exactly those, refusing a lock that no longer matches
`pyproject.toml`. Every entry in the prediction log claims reproducibility
from its `snapshot_id`; that claim covers the data, and the lock covers the
code around it. The package is installed editable, so the commands always run
the checkout, never a stale copy from before the last `git pull`.

Every `fpl-*` command is also `python -m fpl.<package>.<module>`; the two run
the same function. Run them from the repository root, with the virtualenv
active: paths resolve against the working directory, so the commands refuse to
run anywhere else rather than start a second `data/raw/` or a `predictions/`
that is not the log ([ADR 0011](adr/0011-paths-relative-to-the-repository-root.md)).
`fpl-serve` and `fpl-migrate` are the exceptions: they read only the database,
so they run anywhere, including the deployed image.

## Commands

| Command | What it does |
|---|---|
| `fpl-fetch` | Snapshot the API to `data/raw/<timestamp>/` (`--skip-players`: bootstrap and fixtures only, in seconds) |
| `fpl-entry` | Every logged model's entry from one snapshot: written, verified and summarised (`--dry-run`: under `scratch/`) |
| `fpl-predict-baseline`, `fpl-predict-fixture`, `fpl-predict-gbm` | One model's entry (`--out scratch/`: exploratory, never the log) |
| `fpl-train-gbm` | Train the learned model on past seasons and write its held-out report |
| `fpl-verify` | Check an entry against the snapshot named in its own rows; non-zero on any disagreement |
| `fpl-summarise` | A readable digest of an entry, into `reports/` |
| `fpl-score` | Score every committed entry against actual results, into `scores/` |
| `fpl-backtest` | Replay settled gameweeks offline, without leaking the future (`--held-out`: the gate for a new factor) |
| `fpl-optimise` | The best legal squad, XI and captain for one entry's predictions, under the £100m budget, by integer programming (`--squad FILE`: the best transfers from a squad you own, net of hits) |
| `fpl-plan` | Transfers over the next one to six gameweeks (three by default), from a squad or from scratch, with free transfers rolling and later weeks discounted. Later weeks' predictions are form-fixture-v1's, computed for the plan and never logged |
| `fpl-load` | Load a snapshot into Postgres: teams, players, fixtures, club spells and per-fixture stats (needs `DATABASE_URL`) |
| `fpl-features` | Build `player_gameweek_features`, one row of pre-deadline model inputs per player per gameweek (`--rebuild`: all of it) |
| `fpl-backfill` | Load 2016/17 to 2025/26 into Postgres as training data, from a pinned archive (`--download-only`: fetch and check the files) |
| `fpl-stale` | Whether the database is behind a bootstrap-only snapshot, and whether its schema is at this code's head |
| `fpl-project` | Rebuild the database's copy of the log from `predictions/` and `scores/` |
| `fpl-reconcile` | Check the database's copy of the log against git, row by row; non-zero on any difference |
| `fpl-migrate` | Bring the database's schema to the latest migration; the deploy's release command |
| `fpl-serve` | The API over the database, at `http://127.0.0.1:8000`, with interactive docs at `/docs` |

An exploratory entry, end to end, with nothing written to the log:

```bash
fpl-fetch                   # full snapshot, about six minutes
fpl-entry --dry-run         # every logged model: written, verified and summarised under scratch/
```

## Tests and lint

```bash
uv run pytest                    # the suite
make lint                        # ruff check, ruff format --check and mypy, as CI's lint job
uv run pre-commit install        # once: the same checks before each commit
```

The suite is written with `unittest` and runs under pytest. `tests/` mirrors
the package: `tests/api/` tests `fpl/api/`, and so on, with `tests/tools/` for
the scripts in `tools/`, `tests/project/` for checks on the repository itself
(the README's numbers, the ADRs, packaging, layout), and shared helpers in
`tests/support/`. mypy checks `fpl/`, non-strict to start (`[tool.mypy]` in
`pyproject.toml`).

```bash
uv run coverage run -m pytest    # [tool.coverage] measures subprocesses too
uv run coverage combine && uv run coverage report
```

## Database

The schema lives in `fpl/db/models.py`, with Alembic migrations in
`fpl/db/migrations/`. Locally, Postgres runs in Docker:

```bash
make up migrate    # start Postgres, bring the schema up to date
make seed          # load data/raw/LATEST and the committed log into it
make reconcile     # check the database's copy of the log against git
make test-db       # the database tests, against a separate fpl_test database
make psql          # a shell on the local database
```

`fpl-load` writes a snapshot in one transaction, and loading one twice changes
nothing. A snapshot newer than any loaded for its season updates rows; an
older one only fills gaps, so loading out of order never rolls the database
back.

The log tables are a projection of `predictions/` and `scores/`, never a second
record: `fpl-project` rebuilds them from the files, and CI rebuilds them into
an empty database on every PR and runs `fpl-reconcile`, which fails on any row
that differs from git.

After changing `fpl/db/models.py`, generate the migration with
`alembic revision --autogenerate -m "what changed"` and read it before
committing. `tests/db/test_db.py` fails if the models and migrations ever
disagree.

## API

An HTTP API over the database, under `/v1`: gameweeks with their fixtures and
entries, each entry's predictions (filtered by position, club and price, and
paged), players with their clubs, matches and predictions, the logged models,
and the scorecard. Errors are RFC 9457 problem details, and reads carry an
`ETag`, so an unchanged response costs a `304`.

Two routes run the optimiser over a committed entry's predictions:

- `POST /v1/optimise`: the best transfers from the fifteen players in the
  body, with their selling prices, the bank and the free transfers; or, with no
  squad, the best squad from scratch.
- `GET /v1/entries/{fpl_team_id}/advice`: the same for an FPL team, read from
  the public FPL API. Selling prices and free transfers are not public, so they
  are estimated, and the response's `estimates` says how; `?free_transfers=`
  overrides the estimate.

Each request's solves share a ten-second limit (`FPL_SOLVER_TIME_LIMIT`; a
`503` when it runs out), and the two routes allow ten requests a minute per
client (`FPL_SOLVER_RATE`, `off` to disable). Results are cached in Postgres
until the next deploy, and FPL API responses for ten minutes
([ADR 0016](adr/0016-optimise-my-team-caches-beside-the-projection.md)).

```bash
docker compose up        # Postgres and the API; docs at http://localhost:8000/docs
make serve               # or the API from the checkout, reloading on changes
```

The contract is [`openapi.json`](openapi.json), committed and checked against
the code by the tests: regenerate it with `make openapi` after changing a
route. CI serves the API over the projected log and fuzzes it from that
document with [schemathesis](https://schemathesis.io) (`make fuzz` locally).

**Load test.** [`tests/load/optimise.js`](../tests/load/optimise.js), with k6,
on 25 October 2026: one `fpl-serve` process on an Apple Silicon laptop over
Postgres in Docker, four virtual users, a transfer plan for a real GW8 squad.
14,045 requests, none failed.

| `POST /v1/optimise` | Median | p95 | p99 |
|---|---|---|---|
| Solved (three solves each) | 147 ms | 209 ms | 239 ms |
| From the cache | 9 ms | 11 ms | 12 ms |

The deployed machine is a shared vCPU, so expect the solved numbers to be
slower there.

## Web frontend

`web/` is a React and TypeScript site over the API, built into `web/dist` and
served by the API at `/` from the same image
([ADR 0025](adr/0025-the-frontend-is-served-by-the-api.md)). It needs Node 24:

```bash
make web                         # npm ci and build into web/dist
make serve                       # the API, and the site at / once built
cd web && npm run dev            # or Vite's dev server, proxying /v1 to `make serve`
cd web && API_TARGET=https://fpl-predictor-aman.fly.dev npm run dev   # the same, against live data
cd web && npm run gen:api        # after `make openapi`: regenerate the typed client
cd web && npm run e2e            # Playwright against BASE_URL (default 127.0.0.1:8000)
```

The pages: Home (`/`: the next deadline, this gameweek's top picks, the track
record and the full scorecard), Predictions (`/gameweeks/N`), player pages
(`/players/ID`) and My team (`/optimise`). The styles are one stylesheet of
tokens and components, `web/src/styles.css`. Club colours are in
`web/src/clubs.ts`, and the models' display names in `web/src/models.ts`. The
site's name is `SITE_NAME` in `web/src/brand.ts`. Player photos come from
FPL's CDN by the player's code; `PHOTOS` in `web/src/components/bits.tsx` turns
them off.

## Deploying

Every merge to `main` that passes CI deploys the API and the site to Fly.io,
over Postgres on Neon: migrations first, then the committed log projected and
reconciled, then a smoke test of the live URL. The one-time setup is in
[`deploy.md`](deploy.md); monitoring and incidents are in
[`operations.md`](operations.md).
