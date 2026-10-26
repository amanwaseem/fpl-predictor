# Operations

How the hosted service (ADR 0015) is watched, what it promises, and what to do
when something breaks. Deploying it is [`deploy.md`](deploy.md); making an
entry by hand is [`RUNBOOK.md`](../RUNBOOK.md).

## What is watched, and how

| What | How | Alert |
|---|---|---|
| The API is up | `/healthz` hourly, from GitHub Actions (`uptime.yml`, `tools/check_uptime.py`) | Failed-workflow email |
| The API reads its database | `/v1/status` in the same check | Failed-workflow email |
| The data is fresh | `/v1/status`'s `checked_age_seconds`, since the ingest last compared FPL with the database, under 30 hours | Failed-workflow email |
| A load the ingest needed went through | The Ingest workflow fails its own run | Failed-workflow email |
| An entry is in before the deadline | The deadline watchdog (`deadline.yml`, `tools/check_deadline.py`), hourly | Failed-workflow email |
| Errors | Sentry, when `SENTRY_DSN` is set on the Fly app | Sentry's email |
| Latency, solver time, freshness | OpenTelemetry over OTLP, when `OTEL_EXPORTER_OTLP_ENDPOINT` is set | The dashboard |
| Logs | One JSON line per request on stdout, with a request id: `fly logs --app fpl-predictor-aman` | — |

Every response carries `X-Request-ID`. A user who reports a problem can quote
it, and `fly logs` finds the request's line.

Checks are hourly rather than every minute on purpose: the Fly machine stops
when idle and Neon suspends an idle database, and a check that kept them awake
would cost more than an outage it caught sooner. Nothing in the app polls.

## Service levels

Promises to ourselves, measured monthly. Missing one is a reason to look,
not a page at 3am.

| | Objective | Measured by |
|---|---|---|
| **Availability** | 99% of hourly `/healthz` checks answer 200 within 60 s, a cold start included | Uptime workflow history |
| **Read latency** | p95 under 500 ms for `/v1` reads on a warm machine | `http.server.request.duration` by route |
| **Optimiser latency** | p95 under 2 s for `/v1/optimise` and advice, uncached | The same, and `fpl.solver.duration` |
| **Data freshness** | The ingest checks FPL at least daily, so a settled gameweek's results reach production within 24 hours of FPL settling it | Uptime check, `fpl.data.age` |
| **Entries** | Every logged model's entry merged at least 2 hours before each deadline | The deadline watchdog |

The load test (README, "Load test") is the reference for what latency the
code allows; the deployed machine is a shared vCPU and slower.

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

### Errors in Sentry

Each event carries the request id; find the request with
`fly logs | grep <request id>`. A 500 is always a bug: no input may cause one,
which CI's fuzz checks.

## Backups and restore

The production database is a projection plus loaded snapshots (ADR 0015), so
most of it can be rebuilt without a backup: the log from git (`fpl-project`),
past seasons from the pinned archive (`fpl-backfill`), the live season from
the next ingest, and features from those (`fpl-features`). What a backup adds
is speed, and the caches.

Neon keeps a history of every change for its point-in-time restore window
(see the project's **Settings → Storage**; the free plan's is short). A
restore is a new branch of the database as it was at a moment, which leaves
production alone until it is chosen.

### Testing a restore

Done once, and again after any change to the schema's shape:

1. In Neon: **Branches → Create branch**, from `main`, at a time an hour ago.
2. Copy the new branch's direct connection string (pooling off).
3. From the repository root, without the string appearing on screen:

   ```bash
   export DATABASE_URL="$(pbpaste)"
   fpl-reconcile                  # the log matches git, row by row
   fpl-stale --schema-only        # the schema is at the latest migration
   psql "$DATABASE_URL" -c "select season, count(*) from player_fixture_stats group by 1 order by 1"
   unset DATABASE_URL
   ```

4. Delete the branch.
5. Record the date, the restore point, and what the checks said, below.

To restore for real, make the branch the project's default (Neon: **Set as
default**), then update `DATABASE_URL` on Fly and in the `production` GitHub
environment, and redeploy.

### Restores tested

| Date | Restore point | Result |
|---|---|---|
| | | Not yet tested |

## Setting up error reporting and the dashboard

Both are off until their settings are on the Fly app. Copy each value and
set it without it appearing on screen:

```bash
# Sentry: a Python project's DSN (Sentry: Settings → Projects → Client Keys)
fly secrets set SENTRY_DSN="$(pbpaste)" --app fpl-predictor-aman

# OpenTelemetry: an OTLP endpoint and its auth header, for example Grafana
# Cloud's (Grafana Cloud: My Account → OpenTelemetry → Configure)
fly secrets set OTEL_EXPORTER_OTLP_ENDPOINT="$(pbpaste)" --app fpl-predictor-aman
fly secrets set OTEL_EXPORTER_OTLP_HEADERS="$(pbpaste)" --app fpl-predictor-aman
```

Setting a secret restarts the app with it. To check Sentry end to end, send
an induced error from the deployed app; it should appear in Sentry within a
minute:

```bash
fly ssh console --app fpl-predictor-aman -C fpl-check-sentry
```

No public route errors on purpose: a 500 an outsider could cause would be a
bug, which CI's fuzz checks for.

The dashboard is [`docs/dashboard.json`](dashboard.json), for Grafana: import
it and pick the data source the OTLP endpoint writes to.
