"""Warn before a deadline passes with no entry for it.

GW5 was not predicted: its deadline passed while no one was working on the
project, and rule 2 means it stays missed (#33, ADR 0004). CI runs on pushes
and PRs, so it is silent exactly when nobody is working. This runs on a
schedule instead (`.github/workflows/deadline.yml`) and fails when the next
deadline is under WINDOW_HOURS away and `predictions/` holds no entry for that
gameweek. GitHub's failed-workflow email is the alert.

It passes, and says why, when there is an entry, when the deadline is further
off, when the season has no next gameweek, and when the API cannot be read. An
outage is not a missed deadline, but "could not check" is said out loud rather
than passed silently.

It only reads: `bootstrap-static` from the API and the names in
`predictions/`. It never snapshots, writes to the repo, or produces an entry.

Usage, from the repository root:
    python tools/check_deadline.py
    python tools/check_deadline.py --fake-deadline-hours 48 --fake-gameweek 99

The --fake flags replace the API with a deadline that many hours from now, for
checking the alert end to end from a manual workflow run.
"""

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fpl.ingest.fetch import get
from fpl.log.contract import LOG_DIR_NAME, TIMESTAMP, parse_entry_filename

# Three days: room for a snapshot after the press conferences, a PR and a
# merge, with the daily run firing on at least two of them.
WINDOW_HOURS = 72

PASS, FAIL = "pass", "fail"


def next_deadline(bootstrap):
    """(gameweek, deadline as aware UTC datetime) of the is_next event, or None."""
    event = next((e for e in bootstrap.get("events", []) if e.get("is_next")), None)
    if event is None:
        return None
    deadline = datetime.strptime(event["deadline_time"], TIMESTAMP).replace(tzinfo=UTC)
    return event["id"], deadline


def entries_for(gameweek, names):
    """The entry filenames among `names` for `gameweek`. Other files are ignored."""
    found = []
    for name in names:
        try:
            gw, _ = parse_entry_filename(name)
        except ValueError:
            continue
        if gw == gameweek:
            found.append(name)
    return sorted(found)


def check(bootstrap, names, now, window_hours=WINDOW_HOURS):
    """(PASS or FAIL, a sentence saying why) for the next deadline."""
    upcoming = next_deadline(bootstrap)
    if upcoming is None:
        return PASS, "No next gameweek in bootstrap-static: nothing to warn about."
    gameweek, deadline = upcoming
    hours = (deadline - now).total_seconds() / 3600
    when = f"GW{gameweek:02d} deadline {deadline.strftime(TIMESTAMP)}, {hours:.0f}h away"

    found = entries_for(gameweek, names)
    if found:
        return PASS, f"{when}. Entry in predictions/: {', '.join(found)}."
    if hours > window_hours:
        return PASS, f"{when}. No entry yet; that is over {window_hours}h, so no warning."
    return FAIL, (f"{when}, and predictions/ has no gw{gameweek:02d}_*.csv. "
                  "Make the entry now: see RUNBOOK.md. A missed deadline stays missed.")


def fake_bootstrap(hours, gameweek, now):
    """A bootstrap with one is_next event `hours` from `now`."""
    deadline = (now + timedelta(hours=hours)).strftime(TIMESTAMP)
    return {"events": [{"id": gameweek, "deadline_time": deadline, "is_next": True}]}


def report(status, message):
    """Print the result, and add it to the run's summary page on GitHub."""
    line = f"{'OK' if status == PASS else 'DEADLINE AT RISK'}: {message}"
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"### Deadline watchdog\n\n{line}\n")


def main(predictions_dir, now=None, fake_hours=None, fake_gameweek=None):
    now = now or datetime.now(UTC)
    if fake_hours is not None:
        bootstrap = fake_bootstrap(fake_hours, fake_gameweek, now)
        print(f"FAKE deadline: GW{fake_gameweek:02d} in {fake_hours}h, not read from the API.")
    else:
        try:
            bootstrap = get("bootstrap-static/")
        except Exception as e:  # any failure to read is "could not check"
            report(PASS, f"Could not check: bootstrap-static unreadable ({e}). "
                         "Not a missed deadline, but check the next one by hand.")
            return 0
    directory = Path(predictions_dir)
    names = [p.name for p in directory.iterdir()] if directory.is_dir() else []
    status, message = check(bootstrap, names, now)
    report(status, message)
    return 0 if status == PASS else 1


def cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--predictions", default=LOG_DIR_NAME, metavar="DIR",
                    help=f"the log directory to look in (default: {LOG_DIR_NAME}/)")
    ap.add_argument("--fake-deadline-hours", type=float, default=None, metavar="H",
                    help="skip the API: pretend the next deadline is H hours away")
    ap.add_argument("--fake-gameweek", type=int, default=None, metavar="N",
                    help="the gameweek of the fake deadline (with --fake-deadline-hours)")
    args = ap.parse_args(argv)
    if (args.fake_deadline_hours is None) != (args.fake_gameweek is None):
        ap.error("--fake-deadline-hours and --fake-gameweek go together.")
    return main(args.predictions, fake_hours=args.fake_deadline_hours,
                fake_gameweek=args.fake_gameweek)


if __name__ == "__main__":
    sys.exit(cli())
