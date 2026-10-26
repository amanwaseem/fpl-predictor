"""Warn before a deadline passes with no entry for it.

GW5 was not predicted: its deadline passed while no one was working on the
project, and rule 2 means it stays missed (#33, ADR 0004). CI runs on pushes
and PRs, so it is silent exactly when nobody is working. This runs on a
schedule instead (`.github/workflows/deadline.yml`) and fails when the next
deadline is ALARM_HOURS away or less and `main` holds no entry for that
gameweek. GitHub's failed-workflow email is the alert.

Since #68 the entry workflow makes the entry and opens its PR once the
deadline is 12 hours away. This is the alarm for when that did not happen or
its PR sits unmerged, so it fires after the entry workflow's first attempts,
not before: an alarm that goes off every week by design is learned as noise.
When it fails it says which: "merge #N", or "no PR: make the entry by hand".

It passes, and says why, when there is an entry, when the deadline is further
off, when the season has no next gameweek, and when the API cannot be read. An
outage is not a missed deadline, but "could not check" is said out loud rather
than passed silently. A bootstrap that reads but is malformed is different: the
API's schema has changed, the fetch is likely broken too, and that fails.

An entry on main only counts if it is this season's. Entry names carry the
gameweek and not the season, so next season's GW4 would otherwise find this
season's `gw04_*.csv` and pass. Each entry's own `deadline_utc` is compared
with the API's deadline instead; one from another season is reported, not
counted.

The helpers here are shared with `tools/entry_bot.py`, so the watchdog and the
entry workflow read the API and the log the same way.

It only reads: `bootstrap-static` from the API, the entries in `predictions/`
and, when given, the list of open PRs. It never snapshots, writes to the repo,
or produces an entry.

Usage, from the repository root:
    python tools/check_deadline.py
    python tools/check_deadline.py --prs prs.json
    python tools/check_deadline.py --fake-deadline-hours 6 --fake-gameweek 99

`prs.json` is `gh pr list --state all --json number,state,headRefName,files`.
The --fake flags replace the API with a deadline that many hours from now, for
checking the alert end to end from a manual workflow run.
"""

import argparse
import csv
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fpl.ingest.fetch import get
from fpl.log.contract import LOG_DIR_NAME, TIMESTAMP, parse_entry_filename

# The entry workflow acts from 12 hours out and retries hourly. Two hours of
# its retries go by before this fires, and ten hours are left to merge its PR
# or make the entry by hand.
ALARM_HOURS = 10

# Two deadlines for one gameweek this far apart are two seasons. Closer, it is
# the same gameweek with a deadline FPL moved after the entry was made.
SEASON_APART = timedelta(days=120)

PASS, FAIL = "pass", "fail"


class Malformed(Exception):
    """bootstrap-static read, but not in the shape this project depends on."""


def next_deadline(bootstrap):
    """(gameweek, deadline as aware UTC datetime) of the is_next event, or None.

    Raises Malformed when there is more than one is_next event, or the one
    there has no usable id or deadline_time.
    """
    events = bootstrap.get("events") if isinstance(bootstrap, dict) else None
    if not isinstance(events, list):
        raise Malformed("no events list")
    upcoming = [e for e in events if isinstance(e, dict) and e.get("is_next")]
    if not upcoming:
        return None
    if len(upcoming) > 1:
        raise Malformed(f"{len(upcoming)} events are is_next")
    event = upcoming[0]
    gameweek = event.get("id")
    if not isinstance(gameweek, int) or isinstance(gameweek, bool) or gameweek < 1:
        raise Malformed(f"is_next event has id {gameweek!r}")
    try:
        deadline = datetime.strptime(event["deadline_time"], TIMESTAMP).replace(tzinfo=UTC)
    except (KeyError, TypeError, ValueError):
        raise Malformed(f"GW{gameweek} has deadline_time {event.get('deadline_time')!r}") from None
    return gameweek, deadline


def deadline_of(bootstrap, gameweek):
    """The deadline of any gameweek in bootstrap, or None if it is not there."""
    for event in bootstrap.get("events", []):
        if isinstance(event, dict) and event.get("id") == gameweek:
            try:
                return datetime.strptime(event["deadline_time"], TIMESTAMP).replace(tzinfo=UTC)
            except (KeyError, TypeError, ValueError):
                return None
    return None


def season_of(deadline):
    """The season a deadline falls in, as "2026-27". Seasons start in August."""
    start = deadline.year if deadline.month >= 7 else deadline.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


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


def _entry_deadline(path):
    """The deadline_utc an entry was made for, from its first row, or None."""
    try:
        with path.open(newline="") as f:
            row = next(csv.DictReader(f), None)
        return datetime.strptime(row["deadline_utc"], TIMESTAMP).replace(tzinfo=UTC)
    except (OSError, StopIteration, KeyError, TypeError, ValueError, csv.Error):
        return None


def classify_entries(gameweek, deadline, directory):
    """The log's entries for `gameweek`, sorted by whether they are this deadline's.

    Returns a dict of filename lists:
      current       made for this deadline;
      moved         made for this gameweek this season, but FPL has since
                    moved the deadline. Still the entry;
      other_season  made for the same gameweek number in another season;
      unreadable    no deadline_utc that can be read.
    """
    found = {"current": [], "moved": [], "other_season": [], "unreadable": []}
    directory = Path(directory)
    names = [p.name for p in directory.iterdir()] if directory.is_dir() else []
    for name in entries_for(gameweek, names):
        made_for = _entry_deadline(directory / name)
        if made_for is None:
            found["unreadable"].append(name)
        elif made_for == deadline:
            found["current"].append(name)
        elif abs(made_for - deadline) < SEASON_APART:
            found["moved"].append(name)
        else:
            found["other_season"].append(name)
    return found


def prs_adding(gameweek, prs):
    """Open PRs that add an entry for `gameweek`, as [(number, [entry names])].

    Only a file directly in predictions/ is an entry: tests/fixtures/predictions/
    is not the log, and must not stand in for it.
    """
    found = []
    for pr in prs:
        if pr.get("state", "OPEN") != "OPEN":
            continue
        names = [
            Path(f["path"]).name
            for f in pr.get("files", [])
            if Path(f["path"]).parent == Path(LOG_DIR_NAME)
        ]
        entries = entries_for(gameweek, names)
        if entries:
            found.append((pr["number"], entries))
    return found


def fake_bootstrap(hours, gameweek, now):
    """A bootstrap with one is_next event `hours` from `now`."""
    deadline = (now + timedelta(hours=hours)).strftime(TIMESTAMP)
    return {"events": [{"id": gameweek, "deadline_time": deadline, "is_next": True}]}


def load_bootstrap(now, fake_hours=None, fake_gameweek=None):
    """(bootstrap, None), or (None, why it could not be read).

    The fake replaces the API; otherwise any failure to read it is reported,
    not raised. Whether it is well formed is next_deadline's to say.
    """
    if fake_hours is not None:
        print(f"FAKE deadline: GW{fake_gameweek:02d} in {fake_hours}h, not read from the API.")
        return fake_bootstrap(fake_hours, fake_gameweek, now), None
    try:
        return get("bootstrap-static/"), None
    except Exception as e:  # any failure to read is "could not check"
        return None, f"bootstrap-static unreadable ({e})"


def load_prs(path):
    """The PR list from `gh pr list --json`, or [] when no file was given."""
    if path is None:
        return []
    prs = json.loads(Path(path).read_text())
    if not isinstance(prs, list):
        raise ValueError(f"{path}: expected a JSON list of PRs")
    return prs


def check(bootstrap, classified, prs, now, alarm_hours=ALARM_HOURS):
    """(PASS or FAIL, a sentence saying why) for the next deadline.

    `classified` is classify_entries() for the next gameweek, or a function of
    (gameweek, deadline) returning it.
    """
    try:
        upcoming = next_deadline(bootstrap)
    except Malformed as e:
        return FAIL, (
            f"bootstrap-static is malformed ({e}). The API's schema may have "
            "changed; fpl-fetch is likely broken too. Check by hand."
        )
    if upcoming is None:
        return PASS, "No next gameweek in bootstrap-static: nothing to warn about."
    gameweek, deadline = upcoming
    if callable(classified):
        classified = classified(gameweek, deadline)
    hours = (deadline - now).total_seconds() / 3600
    when = f"GW{gameweek:02d} deadline {deadline.strftime(TIMESTAMP)}, {hours:.0f}h away"

    entries = classified["current"] + classified["moved"]
    if entries:
        moved = (
            f" Made for a deadline FPL has since moved: {', '.join(classified['moved'])}."
            if classified["moved"]
            else ""
        )
        return PASS, f"{when}. Entry in predictions/: {', '.join(entries)}.{moved}"

    notes = ""
    if classified["other_season"]:
        notes += f" Not counted, made for another season: {', '.join(classified['other_season'])}."
    if classified["unreadable"]:
        notes += f" Not counted, no readable deadline_utc: {', '.join(classified['unreadable'])}."
    pending = prs_adding(gameweek, prs)
    if pending:
        status_line = "Open PR: " + "; ".join(f"#{n} ({', '.join(e)})" for n, e in pending) + "."
    else:
        status_line = "No open PR adds one."

    if hours > alarm_hours:
        return PASS, f"{when}. No entry on main yet; over {alarm_hours}h. {status_line}{notes}"
    if pending:
        numbers = ", ".join(f"#{n}" for n, _ in pending)
        return FAIL, (
            f"{when}, and main has no gw{gameweek:02d}_*.csv. {status_line} "
            f"Read and merge {numbers} before the deadline.{notes}"
        )
    return FAIL, (
        f"{when}, main has no gw{gameweek:02d}_*.csv, and no open PR adds one: "
        "the entry workflow has not made it. Make the entry by hand now: see "
        f"RUNBOOK.md. A missed deadline stays missed.{notes}"
    )


def report(status, message, title="Deadline watchdog"):
    """Print the result, and add it to the run's summary page on GitHub."""
    line = f"{'OK' if status == PASS else 'DEADLINE AT RISK'}: {message}"
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"### {title}\n\n{line}\n")


def main(predictions_dir, prs_file=None, now=None, fake_hours=None, fake_gameweek=None):
    now = now or datetime.now(UTC)
    bootstrap, unreadable = load_bootstrap(now, fake_hours, fake_gameweek)
    if unreadable:
        report(
            PASS,
            f"Could not check: {unreadable}. "
            "Not a missed deadline, but check the next one by hand.",
        )
        return 0
    prs = load_prs(prs_file)
    status, message = check(
        bootstrap, lambda gw, deadline: classify_entries(gw, deadline, predictions_dir), prs, now
    )
    report(status, message)
    return 0 if status == PASS else 1


def cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--predictions",
        default=LOG_DIR_NAME,
        metavar="DIR",
        help=f"the log directory to look in (default: {LOG_DIR_NAME}/)",
    )
    ap.add_argument(
        "--prs",
        default=None,
        metavar="FILE",
        help="gh pr list --state all --json number,state,headRefName,files",
    )
    ap.add_argument(
        "--fake-deadline-hours",
        type=float,
        default=None,
        metavar="H",
        help="skip the API: pretend the next deadline is H hours away",
    )
    ap.add_argument(
        "--fake-gameweek",
        type=int,
        default=None,
        metavar="N",
        help="the gameweek of the fake deadline (with --fake-deadline-hours)",
    )
    args = ap.parse_args(argv)
    if (args.fake_deadline_hours is None) != (args.fake_gameweek is None):
        ap.error("--fake-deadline-hours and --fake-gameweek go together.")
    return main(
        args.predictions,
        args.prs,
        fake_hours=args.fake_deadline_hours,
        fake_gameweek=args.fake_gameweek,
    )


if __name__ == "__main__":
    sys.exit(cli())
