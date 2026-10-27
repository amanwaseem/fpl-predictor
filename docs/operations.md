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
| Requests and errors | One JSON line per request, and the traceback of any error, with its request id: `fly logs --app fpl-predictor-aman` | — |

Every response carries `X-Request-ID`, a 500 included. A user who reports a
problem can quote it, and `fly logs | grep <id>` finds the request and, for an
error, its traceback.

Checks are hourly rather than every minute on purpose: the Fly machine stops
when idle and Neon suspends an idle database, and a check that kept them awake
would cost more than an outage it caught sooner.

## Service levels

Promises to ourselves, read from the workflows' history. Missing one is a
reason to look, not a page at 3am.

| | Objective | Measured by |
|---|---|---|
| **Availability** | 99% of hourly `/healthz` checks answer 200 within 60 s, a cold start included | Uptime workflow history |
| **Data freshness** | The ingest checks FPL at least daily, so a settled gameweek's results reach production within 24 hours of FPL settling it | Uptime workflow history |
| **Entries** | Every logged model's entry merged at least 2 hours before each deadline | The deadline watchdog |
| **Latency** | Reads well under a second on a warm machine, and the optimiser within its 10-second limit | `duration_ms` in the logs; the load test in the README |

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

The production database holds nothing that exists only there. Everything in
it can be rebuilt from scratch into an empty database, in this order:

```bash
fpl-migrate                     # the schema
fpl-project                     # the log, from predictions/ and scores/ in git
fpl-backfill                    # 2016/17-2025/26, from the pinned archive
# then Actions → Ingest → Run workflow, with force: the live season
fpl-features                    # the feature table, from all of the above
```

The only things a rebuild loses are the caches (optimiser results and FPL API
responses), which refill themselves. So no backup is kept or tested beyond
what Neon does on its own: it keeps a short history of every change, and a
point-in-time branch can be made from its console if a rebuild would ever be
too slow.
