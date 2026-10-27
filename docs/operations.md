# Operations

How the hosted service (ADR 0015) is watched, what it promises, and what to do
when something breaks. Deploying it is [`deploy.md`](deploy.md); making an
entry by hand is [`RUNBOOK.md`](../RUNBOOK.md).

Everything here uses what the project already has, GitHub Actions and Fly's
own logs, with no third-party monitoring service to sign up for or keep.

## What is watched, and how

| What | How | Alert |
|---|---|---|
| The API is up | `/healthz` hourly, from GitHub Actions (`uptime.yml`, `tools/check_uptime.py`) | Failed-workflow email |
| The API reads its database | `/v1/status` in the same check | Failed-workflow email |
| The data is fresh | `/v1/status`'s `checked_age_seconds`, since the ingest last compared FPL with the database, under 30 hours | Failed-workflow email |
| A load the ingest needed went through | The Ingest workflow fails its own run | Failed-workflow email |
| An entry is in before the deadline | The deadline watchdog (`deadline.yml`, `tools/check_deadline.py`), hourly | Failed-workflow email |
| Requests and errors | One JSON line per request, and the traceback of any error, with its request id: `fly logs --app fpl-predictor-aman` | None |

Every response carries `X-Request-ID`, a 500 included. A user who reports a
problem can quote it, and `fly logs | grep <id>` finds the request and, for an
error, its traceback.

Two things this setup does not do, on purpose ([ADR 0021](adr/0021-observability-with-what-the-project-already-has.md)):

- **A 500 sends no alert.** The guard against them is before a deploy: CI's
  fuzz test sends every route inputs it was not written for and fails on any
  500. After a deploy that changes the API, read `fly logs` for a minute.
- **Logs are not kept.** `fly logs` shows Fly's recent buffer, not a history,
  so a request id is findable for a while after the request, not days later.
  Ask whoever reports a problem for the id straight away.

Checks are hourly rather than every minute on purpose: the Fly machine stops
when idle and Neon suspends an idle database, and a check that kept them awake
would cost more than an outage it caught sooner.

## Service levels

Promises to ourselves, read from the workflows' history. Missing one is a
reason to look, not a page at 3am.

| | Objective | Measured by |
|---|---|---|
| **Availability** | 99% of hourly `/healthz` checks answer 200 within 60 s, a cold start included | Uptime workflow history |
| **Data freshness** | The ingest checks FPL within every 30 hours (it is scheduled daily, with room for one late run), so a settled gameweek's results reach production the day FPL settles them | Uptime workflow history: a run fails when the last check is over 30 hours old |
| **Entries** | Every logged model's entry merged at least 2 hours before each deadline | The deadline watchdog |
| **Latency** | Reads under a second on a warm machine; the optimiser stops at its 10-second limit by construction | Spot checks, not a monthly figure: `duration_ms` in the logs, and the load test in the README |

## Incidents

Start with the failing workflow's summary, which says what failed. Then:

### `/healthz` does not answer: the service is down

1. `fly status --app fpl-predictor-aman`: is a machine running, or failing to
   start?
2. `fly logs --app fpl-predictor-aman`: the last lines before it stopped.
3. If the last deploy broke it: `fly releases`, then
   `fly deploy --image <the previous image>` (deploy.md, "Day to day").
4. Fly itself: [status.flyio.net](https://status.flyio.net).

### `/v1/status` fails: the API cannot read its database

1. `curl https://fpl-predictor-aman.fly.dev/readyz` says whether the database
   answers and whether the schema is at the latest migration.
2. Neon: [neonstatus.com](https://neonstatus.com), and the project's dashboard
   for compute hours (the free plan suspends at its monthly limit).
3. A migration that did not run: re-run Actions → Deploy.

### STALE: the data is not fresh

1. Actions → Ingest: did the 05:41 UTC run fail, or not run at all? GitHub
   disables schedules after 60 quiet days (deploy.md, step 5).
2. Run it by hand: Actions → Ingest → Run workflow (tick **force** to load
   even if it thinks nothing changed).
3. FPL's API down or changed: the run's log says which fetch failed.

### The deadline watchdog fires

Follow its message: merge the open entry PR it names, or make the entry by
hand (RUNBOOK.md). A deadline that passes stays missed (ADR 0004).

### A 500 in the logs

Find its traceback with `fly logs | grep <request id>`. A 500 is always a
bug: no input may cause one, which CI's fuzz checks.

## Backups, and rebuilding instead

Nothing that matters exists only in the production database. The log is in
git, past seasons are in a pinned archive, and the live season is in FPL's
API. A rebuild loses only bookkeeping that refills itself: the caches
(optimiser results and FPL API responses), the record of which snapshots were
loaded, and the ingest's check history.

So no backup is kept or tested beyond what Neon does on its own (a short
history of every change, from which its console can make a point-in-time
branch). To rebuild:

1. In Neon, make a new empty database, or a branch, and copy its direct
   connection string (pooling off).
2. From the repository root, without the string appearing on screen:

   ```bash
   export DATABASE_URL="$(pbpaste)"
   python3 tools/check_database_url.py
   fpl-migrate                   # the schema
   fpl-project                   # the log, from predictions/ and scores/ in git
   fpl-backfill                  # 2016/17-2025/26, from the pinned archive
   unset DATABASE_URL
   ```

3. Point the service at it, again from the clipboard: on Fly,
   `fly secrets set DATABASE_URL="$(pbpaste)" --app fpl-predictor-aman`, and in
   the repository, **Settings → Environments → production → DATABASE_URL**.
4. Actions → Ingest → Run workflow, with **force**: it loads the live season
   and builds the feature table. Until it has run, `/v1/status` has no check
   to report, and the uptime check says STALE.
5. Actions → Deploy → Run workflow, then `curl .../readyz`.
