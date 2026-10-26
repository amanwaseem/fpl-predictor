# fpl-predictor

Gameweek-level points prediction and squad optimisation for Fantasy Premier League, with a public
prediction log that cannot be edited after the fact.

Every prediction is written to an append-only log **before** the gameweek deadline, then scored
against the actual result once the gameweek completes. The log is committed to this repository
and never rewritten, so the model's track record, including the weeks it gets things wrong, is
public and verifiable.

**Contents:** [Why](#why) · [How it works](#how-it-works) · [Approach](#approach) ·
[Current status](#current-status) · [Usage](#usage) · [Repository layout](#repository-layout) ·
[Development](#development) · [Decisions](#decisions) · [Data](#data)

## Why

Most published FPL models report accuracy on a historical holdout set. That measures how well a
model fits the past, not how well it predicts the future, and it gives the author every
opportunity to tune against the test set until the numbers look good.

This project commits its predictions ahead of time instead. The claim is falsifiable and the
receipts are in the git history.

## How it works

Each gameweek goes through the same loop:

1. **Snapshot.** The FPL API is saved to disk, timestamped and never edited, after the final
   press conferences and before the deadline.
2. **Predict.** Every logged model predicts every player's points from that one snapshot.
3. **Verify.** Each entry is checked against the snapshot that produced it before it can be
   committed.
4. **Commit.** The entries are merged into `predictions/` before the deadline, through a PR
   that CI checks.
5. **Score.** Once the gameweek settles, every entry is scored against what actually happened,
   beside naive comparators, and the results go in `scores/`.

Three rules hold the loop together, and are enforced by code rather than by memory:

| Rule | Why |
|---|---|
| A committed prediction is never rewritten | A log that can be edited proves nothing. CI fails any change to a committed entry |
| Nothing after the deadline is used to predict a gameweek | A missed deadline stays missed; a late entry is not evidence |
| Raw data is never committed | Snapshots are large and regenerable; `data/raw/` is gitignored |

## Approach

Rather than regressing directly on total points, the system predicts each scoring component
separately and sums them. That is the design; the "Today" column says how far it has got.

| Component | What it means | Today |
|---|---|---|
| Minutes | Gates everything else: a player who doesn't start scores nothing | Recent minutes, scaled by availability; a dedicated model is planned (#51) |
| Goals | Conditioned on expected minutes | Part of attacking returns, scaled by the side's expected goals in the fixture against its usual |
| Assists | Conditioned on expected minutes | As goals |
| Clean sheet | Team-level, shared across defenders and goalkeeper | Probability from opponent-aware team strength |
| Saves | Goalkeepers only | Recent rate, shrunk toward last season; a fixture-aware version is a candidate (#48) |
| Defensive contribution | Tackles, interceptions, blocks, recoveries | Recent rate, shrunk toward last season; a dedicated model did not pass its gates (#45) |
| Bonus | Derived from the Bonus Points System | Recent rate, shrunk toward last season; a BPS-based model is planned (#50) |

"Today" is `form-fixture-v1`. Components are individually interpretable and can be improved
independently. A monolithic points regressor gives you a single number and no way to tell which
part of it is wrong.

Squad selection is then a constrained optimisation over the predicted points: maximise expected
return subject to the £100m budget, valid formation, the maximum of three players per club, and
the points cost of transfers.

## Current status

Logging predictions for the 2026/27 season. Entries are on `main` for GW4, GW6 and GW8; GW4 and
GW6 are scored, and GW8 is scored once it settles. GW5 and GW7 were missed and stay missed,
recorded as missed in `scores/`.

**Logged models**, each with a role (ADR 0020): `baseline-v1` the fixed yardstick, the main model,
and at most one challenger. A challenger that beats the main model replaces it, and the beaten
one retires: no longer logged, its record kept.

| Model | Logged from | What it does |
|---|---|---|
| `baseline-v1` | GW4 | Rolling form: recent minutes and scoring rate, adjusted for availability and fixture count. No training |
| `form-fixture-v1` | GW6 | The baseline plus last-season priors and opponent-aware team strength |
| `gbm-v1` | GW9 | LightGBM on ten past seasons: minutes in two stages, points by a Tweedie model; held-out report in `reports/gbm-v1.md` (ADR 0019) |

The rolling-form baseline exists so that the pipeline, optimiser and frontend can be built and
validated end to end without waiting on model accuracy. Any model that beats the baseline
replaces it; any model that doesn't, doesn't ship.

**Results so far**

Mean absolute error per player, against FPL's own `form` as a naive comparator, and the actual
points of each one's best predicted XI. Lower MAE is better. From `scores/gw*_*.json`, harness-v1.

| Gameweek | Model | MAE | Spearman | `form` MAE | XI points | `form` XI points |
|---|---|---|---|---|---|---|
| GW4 | `baseline-v1` | 1.153 | 0.713 | 1.277 | 56 | 32 |
| GW6 | `baseline-v1` | 1.031 | 0.747 | 1.228 | 49 | 38 |
| GW6 | `form-fixture-v1` | 1.028 | 0.744 | 1.228 | 30 | 38 |

Both models beat `form` on per-player error every gameweek so far. The XI is another matter:
`form-fixture-v1`'s GW6 XI scored less than `form`'s. Two gameweeks are too few to tell the
two models apart; the cumulative record is `scores/cumulative.json`.

**Working**

- Raw data ingestion from the FPL API (unofficial and undocumented), written to immutable
  timestamped snapshots
- Prediction log: a declared schema, validated before every write, with the append-only rule
  enforced mechanically in CI rather than by discipline
- `fpl-entry`: every logged model's entry from one snapshot, written, verified and summarised in
  one command
- Pre-commit verification of an entry against the snapshot that produced it
- Human-readable digests of a committed entry
- Scoring harness (`fpl-score`, since 22 Sep): every committed entry against actual results,
  beside naive comparators, with the points its best XI would have captured
- Backtest bench (`fpl-backtest`): replays settled gameweeks without leaking the future, and
  judges each candidate held out before it can be logged
- Deadline watchdog: a scheduled check that warns when a deadline is near with no entry
- Postgres: the schema under Alembic migrations, snapshots loaded into it (`fpl-load`), and the
  log projected into it and reconciled against git in CI (`fpl-project`, `fpl-reconcile`)
- A read API under `/v1`, live at [fpl-predictor-aman.fly.dev](https://fpl-predictor-aman.fly.dev/docs),
  deployed on every green merge to `main` and fuzzed from its OpenAPI in CI
- Scheduled ingest: a daily workflow snapshots the API and loads it into production, so
  results and settled gameweeks reach the live API without anyone running `fpl-load`
- Squad optimiser (`fpl-optimise`): the best legal squad, XI and captain for one gameweek, by
  integer programming, checked against an exhaustive search; and from a squad already owned,
  the transfers that pay for themselves after free transfers and hits
- Past seasons as training data: 2016/17 to 2025/26, about 254,000 player-fixture rows, loaded
  from a pinned archive by `fpl-backfill` and checked against pinned counts (ADR 0018)
- A feature table: one row per player per gameweek, from data before its deadline, for all ten
  past seasons and this one (`fpl-features`), leak-tested by corrupting everything after the cut
- Operations: JSON logs with request ids, Sentry and OpenTelemetry when configured, `/v1/status`
  for data freshness, an hourly uptime and freshness check, and SLOs with an incident runbook
  ([`docs/operations.md`](docs/operations.md))
- "Optimise my team" on the API: `POST /v1/optimise` for a squad sent in, and
  `GET /v1/entries/{id}/advice`, which reads an FPL team's squad from the FPL API first

**In progress**

- Automated entries: a scheduled workflow that makes each entry and opens its PR before the
  deadline (#68)

**Planned** (tracked in #75)

- A multi-gameweek horizon for the optimiser (#55)
- Web frontend (#10)

## Usage

> **Making a real entry?** Follow [`RUNBOOK.md`](RUNBOOK.md), not this section. It covers when to
> take the snapshot and why that window has a hard edge on both sides, what to read in the
> output before trusting it, and what to do when the fetch fails near a deadline. The commands
> below are the middle of that procedure.

### Setup

With [uv](https://docs.astral.sh/uv/):

```bash
uv sync --locked && source .venv/bin/activate            # development: everything, with the tests
uv sync --locked --no-dev && source .venv/bin/activate   # making an entry: requests alone
```

An entry is made in the second, the environment the entry workflow uses, so a prediction that
imported anything beyond `requests` would fail there rather than work by accident. The other
commands need more, in dependency groups in `pyproject.toml`: `--group db` for the database
commands, `--group optimise` for `fpl-optimise` (the HiGHS solver), and `--group api` for
`fpl-serve`, which runs the solver too. The default, `dev`, is all of them with the test tools.
`uv sync` is exact, so name every group wanted: `--no-dev --group db --group optimise`.

Every version, transitive ones included, is in `uv.lock`, and `--locked` installs exactly those,
refusing a lock that no longer matches `pyproject.toml`.
Every entry in the prediction log claims reproducibility from its `snapshot_id`; that claim covers
the data, and the lock covers the code around it. The package is installed editable, so the commands always run the checkout, never a
stale copy from before the last `git pull`.

Every `fpl-*` command is also `python -m fpl.<package>.<module>`; the two run the same function. Run them
from the repository root, with the virtualenv active: paths resolve against the working directory, so
the commands refuse to run anywhere else rather than start a second `data/raw/` or a `predictions/`
that is not the log. `fpl-serve` and `fpl-migrate` are the exceptions: they read only the database,
so they run anywhere, including the deployed image.

### Commands

| Command | What it does |
|---|---|
| `fpl-fetch` | Snapshot the API to `data/raw/<timestamp>/` (`--skip-players`: bootstrap and fixtures only, in seconds) |
| `fpl-entry` | Every logged model's entry from one snapshot: written, verified and summarised (`--dry-run`: under `scratch/`) |
| `fpl-predict-baseline`, `fpl-predict-fixture` | One model's entry (`--out scratch/`: exploratory, never the log) |
| `fpl-verify` | Check an entry against the snapshot named in its own rows; non-zero on any disagreement |
| `fpl-summarise` | A readable digest of an entry, into `reports/` |
| `fpl-score` | Score every committed entry against actual results, into `scores/` |
| `fpl-optimise` | The best legal squad, XI and captain for one entry's predictions, under the £100m budget, by integer programming (`--squad FILE`: the best transfers from a squad you own, net of hits) |
| `fpl-backtest` | Replay settled gameweeks offline, without leaking the future (`--held-out`: the gate for a new factor) |
| `fpl-load` | Load a snapshot into Postgres: teams, players, fixtures, club spells and per-fixture stats (needs `DATABASE_URL`) |
| `fpl-features` | Build `player_gameweek_features`, one row of pre-deadline model inputs per player per gameweek, for every season in the database (`--rebuild`: all of it) |
| `fpl-backfill` | Load 2016/17 to 2025/26 into Postgres as training data, from a pinned archive (`--download-only`: fetch and check the files) |
| `fpl-stale` | Whether the database is behind a bootstrap-only snapshot, and whether its schema is at this code's head; the scheduled ingest's first check |
| `fpl-project` | Rebuild the database's copy of the log from `predictions/` and `scores/` |
| `fpl-reconcile` | Check the database's copy of the log against git, row by row; non-zero on any difference |
| `fpl-migrate` | Bring the database's schema to the latest migration; the deploy's release command |
| `fpl-serve` | The read API over the database, at `http://127.0.0.1:8000`, with interactive docs at `/docs` |


### Making an entry

```bash
fpl-fetch                   # full snapshot, about six minutes
fpl-entry --dry-run         # everything under scratch/, to read first
fpl-entry                   # the real entries, into predictions/ and reports/
```

Or one model by hand:

```bash
fpl-predict-baseline --gw 4 --out scratch/    # exploratory, never the log
fpl-predict-baseline --gw 4                   # writes predictions/gw04_baseline-v1.csv
fpl-verify predictions/gw04_baseline-v1.csv   # run before committing
fpl-summarise predictions/gw04_baseline-v1.csv --out reports/
```

`fpl-verify` is the last check before an entry becomes permanent, because after the commit
nothing can be corrected.

## Repository layout

| Path | What it holds |
|---|---|
| `fpl/` | The package: `ingest`, `models`, `log`, `evaluation` (scoring and backtesting), `db`, `api`, and the optimiser |
| `predictions/` | **The log.** Append-only; never rewritten |
| `scores/` | Scored results, derived from the log; rewritten whenever scoring changes |
| `reports/` | Readable digests of each entry, also derived |
| `data/raw/` | Snapshots, gitignored. `data/raw/LATEST` names the most recent |
| `scratch/` | Exploratory runs, gitignored. Never a real prediction |
| `tests/` | The test suite |
| `tools/` | CI and scheduled checks: rule 1, the deadline watchdog |
| `loadtest/` | The k6 load test of `/v1/optimise` |
| `docs/adr/` | Why the project is built the way it is |
| `docs/openapi.json` | The API's contract, generated from the code and committed |
| `docs/deploy.md`, `fly.toml` | Deploying the API: the one-time setup, and Fly's configuration |
| [`RUNBOOK.md`](RUNBOOK.md) | The procedure for one entry, step by step |
| [`SPEC.md`](SPEC.md) | The full requirements |

## Development

```bash
uv run pytest                    # the suite
make lint                        # ruff check, ruff format --check and mypy, as CI's lint job
uv run pre-commit install        # once: ruff, ruff format and mypy before each commit
```

The suite is written with `unittest` and runs under pytest unchanged; `python -m unittest
discover tests` still works. mypy checks `fpl/`, non-strict to start ([tool.mypy] in
`pyproject.toml`). The tools are in the `dev` group, apart from the runtime, so the environment
that produces a log entry stays `requests` alone.

```bash
uv run coverage run -m pytest    # [tool.coverage] measures subprocesses too
uv run coverage combine && uv run coverage report
```

### Database

The schema lives in `fpl/db/models.py`, with Alembic migrations in `fpl/db/migrations/`. Locally,
Postgres runs in Docker:

```bash
make up migrate    # start Postgres, bring the schema up to date
make seed          # load data/raw/LATEST and the committed log into it
make reconcile     # check the database's copy of the log against git
make test-db       # migrations and constraints, against a separate fpl_test database
make psql          # a shell on the local database
```

`fpl-load` writes a snapshot in one transaction, and loading one twice changes nothing. A snapshot
newer than any loaded for its season updates rows; an older one only fills gaps, so loading out
of order never rolls the database back.

The log tables are a projection of `predictions/` and `scores/`, never a second record:
`fpl-project` rebuilds them from the files, and CI rebuilds them into an empty database on every
PR and runs `fpl-reconcile`, which fails on any row that differs from git.

### API

An HTTP API over the database, under `/v1`: gameweeks with their fixtures and entries,
each entry's predictions (filtered by position, club and price, and paged), players with their
clubs, matches and predictions, the logged models, and the scorecard. Errors are RFC 9457 problem
details, and reads carry an `ETag`, so an unchanged response costs a `304`.

Two routes run the optimiser over a committed entry's predictions:

- `POST /v1/optimise`: the best transfers from the fifteen players in the body, with their
  selling prices, the bank and the free transfers; or, with no squad, the best squad from scratch.
- `GET /v1/entries/{fpl_team_id}/advice`: the same for an FPL team, read from the public FPL
  API. Selling prices and free transfers are not public, so they are estimated, and the response's
  `estimates` says how; `?free_transfers=` overrides the estimate.

Each request's solves share a ten-second limit (`FPL_SOLVER_TIME_LIMIT`; a `503` when it runs
out), and the two routes allow ten requests a minute per client (`FPL_SOLVER_RATE`, `off` to
disable). Results are cached in Postgres until the next deploy, and FPL API responses for ten
minutes ([ADR 0016](docs/adr/0016-optimise-my-team-caches-beside-the-projection.md)).

**Load test.** [`loadtest/optimise.js`](loadtest/optimise.js), with k6, on 25 October 2026: one
`fpl-serve` process on an Apple Silicon laptop over Postgres in Docker, four virtual users, a
transfer plan for a real GW8 squad. 14,045 requests, none failed.

| `POST /v1/optimise` | Median | p95 | p99 |
|---|---|---|---|
| Solved (three solves each) | 147 ms | 209 ms | 239 ms |
| From the cache | 9 ms | 11 ms | 12 ms |

The deployed machine is a shared vCPU, so expect the solved numbers to be slower there.

```bash
docker compose up        # Postgres and the API; docs at http://localhost:8000/docs
make serve               # or the API from the checkout, reloading on changes
```

Every merge to `main` that passes CI deploys the API to Fly.io over Postgres on Neon: migrations
first, then the committed log projected and reconciled, then a smoke test of the live URL. The
one-time setup is in [`docs/deploy.md`](docs/deploy.md).

The contract is [`docs/openapi.json`](docs/openapi.json), committed and checked against the code
by the tests: regenerate it with `make openapi` after changing a route. CI serves the API over the
projected log and fuzzes it from that document with [schemathesis](https://schemathesis.io).

### Migrations

After changing a model, generate the migration with
`alembic revision --autogenerate -m "what changed"` and read it before committing.
`tests/test_db.py` fails if the models and migrations ever disagree.

## Decisions

Why the project is built the way it is (an append-only log, snapshots instead of live calls,
held-out backtests as the gate) is recorded one decision at a time in
[`docs/adr/`](docs/adr/README.md), with what would justify revisiting each.

## Data

Sourced from the Fantasy Premier League API, which is unofficial and undocumented: its schema
can change without notice, which is why everything works from snapshots rather than live calls.
This project is unaffiliated with the Premier League or with Fantasy Premier League.

Snapshots are written to `data/raw/<timestamp>/` and are gitignored: they are large and fully
regenerable. `data/raw/LATEST` points at the most recent one.

A snapshot also pulls `event/<gw>/live/` for every settled gameweek, the scoring harness's
source of actual points. It is fetched only once a gameweek's `data_checked` is true, because
bonus points are provisional until then. One request per gameweek rather than per player, so
scoring never needs a six-minute refetch to find out what happened. `manifest.json` records
which gameweeks a snapshot has live data for.

## Licence

MIT
