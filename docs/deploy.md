# Deploying the API

The API runs on [Fly.io](https://fly.io) over Postgres on [Neon](https://neon.tech).
After the one-time setup below, every merge to `main` that passes CI deploys itself
(`.github/workflows/deploy.yml`). Why it is built this way is in
[ADR 0015](adr/0015-a-hosted-service-over-a-rebuildable-projection.md).

**Cost.** Neon's free plan covers this project. Fly needs a card on file and bills
for machine time. The machine stops when idle and is the smallest size, so expect
cents to a few dollars a month.

## One-time setup

### 1. The database, on Neon

1. Sign up at [neon.tech](https://neon.tech) and create a project. Name it
   `fpl-predictor`, choose Postgres 17, and pick **AWS US West 2 (Oregon)**, Neon's
   nearest region to Fly's `sjc` (San Jose) in `fly.toml`. The API makes several
   queries per request, so the two should be close.
2. On the project dashboard, open **Connect** and copy the connection string with
   **connection pooling off**. It looks like:

   ```
   postgresql://neondb_owner:...@ep-something-123456.us-west-2.aws.neon.tech/neondb?sslmode=require
   ```

   This is `DATABASE_URL` below. Use the direct string, not the pooled one
   (`-pooler` in the host): migrations and psycopg's prepared statements both
   want a direct connection.

### 2. The app, on Fly.io

1. Install `flyctl` (`brew install flyctl`), then run `fly auth signup` (or
   `fly auth login`). Add a card when it asks.
2. Pick an app name. Names are global, so `fpl-predictor` may be taken. Put
   yours in `fly.toml` (`app = "..."`) and commit it.
3. From the repository root, create the app without deploying it:

   ```bash
   fly apps create <your-app-name>
   ```

4. Give the app its database:

   ```bash
   fly secrets set DATABASE_URL='<the Neon connection string>' --app <your-app-name>
   ```

5. Create a deploy token for CI, and keep its output for the next step:

   ```bash
   fly tokens create deploy --app <your-app-name>
   ```

### 3. The secrets, on GitHub

In the repository: **Settings → Environments → New environment**, named
`production`. Add two **environment secrets**:

| Secret | Value |
|---|---|
| `FLY_API_TOKEN` | The deploy token from `fly tokens create deploy`, including its `FlyV1 ` prefix |
| `DATABASE_URL` | The same Neon connection string |

The deploy uses `DATABASE_URL` to project the committed log into production and
reconcile it.

### 4. The first deploy

Run **Actions → Deploy → Run workflow** on `main`. It deploys the image (running
`fpl-migrate` first), projects the log, reconciles it, and smoke-tests
`/readyz` and `/v1/scorecard`. The run's summary links the live docs:
`https://<your-app-name>.fly.dev/docs`.

From then on, every green CI run on `main` deploys.

### 5. The football data

Snapshots are not in git, so the deploy cannot load them. Fixtures, players and
which gameweeks have settled (which the scorecard needs to show missed weeks)
are loaded from a machine that has a snapshot:

```bash
DATABASE_URL='<the Neon connection string>' fpl-load
```

Run it again after each new snapshot until ingestion is scheduled.

## Day to day

| To | Run |
|---|---|
| Watch the app's logs | `fly logs --app <your-app-name>` |
| See machines and their state | `fly status --app <your-app-name>` |
| Redeploy without a merge | Actions → Deploy → Run workflow |
| Roll back to the previous image | `fly releases --app <your-app-name>`, then `fly deploy --image <its image>` |
| Rotate the database password | Reset it in Neon, then update `fly secrets` and the GitHub secret |

A failed migration stops a deploy before any new machine takes traffic: the old
version keeps serving, and the failing step's log says why.

A `DATABASE_URL` that cannot work is refused before anything connects, and
the message names what is wrong without ever printing the value, since it
holds the password. Two levels:

- **Anywhere**, only what cannot connect: no scheme or not Postgres, a URL that
  cannot be read, a bad port, quotes or spaces around it. A missing user,
  password, host or database is allowed, since libpq can fill those in.
- **For the hosted database**, all four must be given: by the deploy's first
  step on the GitHub secret, and on Fly, where `fly.toml` sets `FPL_HOSTED`, by
  `fpl-migrate` in the release step and `fpl-serve` at startup.

Neon's pooled `-pooler` host is fine for the API, but `fpl-migrate` and the
deploy's check refuse it: migrations need the direct connection. To check a
string yourself without it appearing on screen, copy it and run:

```bash
DATABASE_URL="$(pbpaste)" python3 tools/check_database_url.py
```
