"""Is the API up, and is its data fresh? (#86, docs/operations.md)

    python tools/check_uptime.py
    python tools/check_uptime.py --url https://fpl-predictor-aman.fly.dev

Run hourly by `.github/workflows/uptime.yml`, from outside Fly. It fails, and
GitHub's failed-workflow email is the alert, when:

- `/healthz` does not answer 200 within TIMEOUT seconds, a cold start
  included: the service is down;
- `/v1/status` does not answer 200: the API is up but cannot read its
  database;
- the daily ingest (#93) has not compared FPL with production for
  STALE_HOURS: it has not run, or could not reach the database.

Freshness is the ingest's last check, not the newest snapshot loaded: on a
day when nothing has changed the ingest loads nothing, and the snapshot stays
days old while the data is current. An ingest that sees FPL ahead and then
fails to load fails its own run, which is its own alert.

The deadline watchdog (tools/check_deadline.py) is the other alarm: a
deadline near with no entry. Hourly, not every minute: the machine stops when
idle and the database suspends, and a check that kept them awake would cost
more than an outage it caught a few minutes sooner.

Standard library only, apart from the FPL fetch helper.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from fpl.ingest.fetch import get

URL = "https://fpl-predictor-aman.fly.dev"
TIMEOUT = 60
# The ingest runs daily; a day and a quarter allows for a late run.
STALE_HOURS = 30


def fetch_json(url, timeout=TIMEOUT):
    """(status, body or None) for a GET of `url`; status 0 if it did not answer."""
    request = urllib.request.Request(url, headers={"User-Agent": "fpl-predictor uptime check"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, None


def settled_by_fpl(bootstrap):
    done = [e["id"] for e in bootstrap["events"] if e.get("finished") and e.get("data_checked")]
    return max(done, default=None)


def check(health, status, fpl_settled):
    """(ok, lines) from the API's answers and FPL's latest settled gameweek."""
    lines = []
    code, _ = health
    if code != 200:
        return False, [f"/healthz answered {code or 'nothing'}: the service is down."]
    lines.append("/healthz: 200")
    code, body = status
    if code != 200 or body is None:
        return False, lines + [
            f"/v1/status answered {code or 'nothing'}: the API cannot read its database."
        ]
    age = body.get("checked_age_seconds")
    hours = None if age is None else age / 3600
    lines.append(
        f"/v1/status: last ingest check {'never' if hours is None else f'{hours:.1f} hours ago'}"
        f", latest settled GW{body.get('latest_settled_gameweek')}, next "
        f"GW{body.get('next_gameweek')} with entries {body.get('next_gameweek_entries')}"
    )
    if fpl_settled is not None:
        lines.append(f"FPL's latest settled: GW{fpl_settled}")
    if hours is None or hours > STALE_HOURS:
        return False, lines + [
            f"STALE: the ingest has not checked FPL in {STALE_HOURS} hours. Check the Ingest "
            "workflow (docs/operations.md)."
        ]
    return True, lines


def main(url=URL):
    health = fetch_json(f"{url}/healthz")
    status = fetch_json(f"{url}/v1/status") if health[0] == 200 else (0, None)
    try:
        fpl_settled = settled_by_fpl(get("bootstrap-static/"))
    except Exception as e:  # noqa: BLE001 - FPL down is not our outage; said, not failed
        fpl_settled = None
        print(f"FPL's API could not be read ({type(e).__name__}); freshness checked by age only.")
    ok, lines = check(health, status, fpl_settled)
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as out:
            out.write(
                f"### Uptime: {'ok' if ok else 'FAILING'}\n\n"
                + "\n".join(f"- {line}" for line in lines)
                + "\n"
            )
    return 0 if ok else 1


def cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=URL, help=f"the API's base URL (default: {URL})")
    args = ap.parse_args(argv)
    return main(args.url.rstrip("/"))


if __name__ == "__main__":
    sys.exit(cli())
