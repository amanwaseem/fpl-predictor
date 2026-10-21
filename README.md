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

Logging predictions for the 2026/27 season. Entries are on `main` for GW4 and GW6; GW5 and GW7
were missed and stay missed, recorded as missed in `scores/`.

**Logged models**

| Model | Logged from | What it does |
|---|---|---|
| `baseline-v1` | GW4 | Rolling form: recent minutes and scoring rate, adjusted for availability and fixture count. No training |
| `form-fixture-v1` | GW6 | The baseline plus last-season priors and opponent-aware team strength |

The rolling-form baseline exists so that the pipeline, optimiser and frontend can be built and
validated end to end without waiting on model accuracy. Any model that beats the baseline
replaces it; any model that doesn't, doesn't ship.

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

**In progress** (to 3 Nov)

- Automated entries: a scheduled workflow that makes each entry and opens its PR before the
  deadline (#68)
- A static scorecard and JSON API on GitHub Pages (#56, #9)
- Squad optimiser, first slice: the best legal squad and captain for one gameweek (#53)

**Planned**

- Further component models: minutes, saves, defensive contribution and bonus (see Approach)
- Optimiser transfers and a multi-gameweek horizon (#54, #55)
- Web frontend (#10)

## Usage

> **Making a real entry?** Follow [`RUNBOOK.md`](RUNBOOK.md), not this section. It covers when to
> take the snapshot and why that window has a hard edge on both sides, what to read in the
> output before trusting it, and what to do when the fetch fails near a deadline. The commands
> below are the middle of that procedure.

### Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt      # pinned dependencies, then this package, editable
```

Dependencies are pinned, transitive ones included. Every entry in the prediction log claims
reproducibility from its `snapshot_id`; that claim covers the data, and the pin covers the code
around it. The package is installed editable, so the commands always run the checkout, never a
stale copy from before the last `git pull`.

Run every command from the repository root, with the virtualenv active. Paths resolve against
the working directory, so the commands refuse to run anywhere else rather than start a second
`data/raw/` or a `predictions/` that is not the log.

### Commands

| Command | What it does |
|---|---|
| `fpl-fetch` | Snapshot the API to `data/raw/<timestamp>/` (`--skip-players`: bootstrap and fixtures only, in seconds) |
| `fpl-entry` | Every logged model's entry from one snapshot: written, verified and summarised (`--dry-run`: under `scratch/`) |
| `fpl-predict-baseline`, `fpl-predict-fixture` | One model's entry (`--out scratch/`: exploratory, never the log) |
| `fpl-verify` | Check an entry against the snapshot named in its own rows; non-zero on any disagreement |
| `fpl-summarise` | A readable digest of an entry, into `reports/` |
| `fpl-score` | Score every committed entry against actual results, into `scores/` |
| `fpl-backtest` | Replay settled gameweeks offline, without leaking the future (`--held-out`: the gate for a new factor) |
| `fpl-load` | Load a snapshot into Postgres: teams, players, fixtures, club spells and per-fixture stats (needs `DATABASE_URL`) |

Every `fpl-*` command is also `python -m fpl.<module>`; the two run the same function.

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
| `fpl/` | The package: ingestion, models, the prediction log, scoring and backtesting |
| `predictions/` | **The log.** Append-only; never rewritten |
| `scores/` | Scored results, derived from the log; rewritten whenever scoring changes |
| `reports/` | Readable digests of each entry, also derived |
| `data/raw/` | Snapshots, gitignored. `data/raw/LATEST` names the most recent |
| `scratch/` | Exploratory runs, gitignored. Never a real prediction |
| `tests/` | The test suite |
| `tools/` | CI and scheduled checks: rule 1, the deadline watchdog |
| `docs/adr/` | Why the project is built the way it is |
| [`RUNBOOK.md`](RUNBOOK.md) | The procedure for one entry, step by step |
| [`SPEC.md`](SPEC.md) | The full requirements |

## Development

```bash
python -m unittest discover tests
```

No third-party test runner. `python -m unittest discover -s tests -t .` and a bare
`python -m unittest` from the repository root work too, and all three run the same suite.

CI also lints and measures coverage. Those tools are pinned apart from the runtime, in
`requirements-dev.txt`, so the environment that produces a log entry stays `requests` alone:

```bash
pip install -r requirements-dev.txt
ruff check .                              # lint; [tool.ruff] in pyproject.toml says which rules and why
coverage run -m unittest discover tests   # [tool.coverage] measures subprocesses too
coverage combine && coverage report
```

### Database

The schema lives in `fpl/db/models.py`, with Alembic migrations in `fpl/db/migrations/`. Locally,
Postgres runs in Docker:

```bash
make up migrate    # start Postgres, bring the schema up to date
make seed          # load data/raw/LATEST into it (fpl-load)
make test-db       # migrations and constraints, against a separate fpl_test database
make psql          # a shell on the local database
```

`fpl-load` writes a snapshot in one transaction, and loading one twice changes nothing. A snapshot
newer than any loaded for its season updates rows; an older one only fills gaps, so loading out
of order never rolls the database back.

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
