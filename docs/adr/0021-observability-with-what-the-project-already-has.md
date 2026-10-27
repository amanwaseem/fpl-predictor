# 0021. Observability with what the project already has

- **Status:** Accepted
- **Date:** 2026-10-26
- **Issues:** #86, #111, #112

## Context

#86 asked for structured logs, error reporting, metrics and traces, a
dashboard, uptime checks, service levels and a tested restore. #111 built it
with Sentry for errors and OpenTelemetry to Grafana Cloud for metrics and
traces.

Each of those is a third-party account to sign up for, keep credentials for,
and learn. For one maintainer and a service with little traffic, that is more
to run than the service itself, and it was scope the project did not need.

## Decision

Observability uses what the project already has, and nothing that needs an
account of its own:

- **Logs:** one JSON line per request, with a request id that every response
  returns, read with `fly logs`.
- **Alerts:** GitHub's emails about failed workflows. An hourly uptime
  workflow checks `/healthz`, and checks `/v1/status` for a database that
  answers and an ingest that ran within 30 hours. The deadline watchdog and
  the ingest fail their own runs.
- **Service levels:** read from the workflows' history.
- **Backups:** none beyond Neon's own history, because everything in the
  database can be rebuilt from git, the pinned archive and FPL's API
  (docs/operations.md has the steps).

Sentry, OpenTelemetry and the dashboard were removed in #112.

## Consequences

- A 500 sends no alert. CI's fuzz test is the guard, before a deploy rather
  than after.
- Logs are Fly's recent buffer, not a history: a request id can be traced for
  a while, not days later.
- Latency is spot-checked rather than measured as a monthly percentile.

## Revisit if

The service has users who would notice a 500 before the maintainer does, or
an incident needs logs older than Fly's buffer. Error reporting is then the
first thing to add back.
