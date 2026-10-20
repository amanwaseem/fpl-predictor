"""Decide whether the entry workflow should make an entry now.

GW5 and GW7 were missed the same way: nobody started the entry. The watchdog
(`tools/check_deadline.py`) warned before GW7 and nothing happened. The entry
workflow (`.github/workflows/entry.yml`, #68) runs hourly and calls this first,
so the PR is already open when a human looks.

It acts when the next deadline is WINDOW_HOURS away or less and has not passed,
and nothing for that gameweek exists yet. Hourly runs and this check together
mean a skipped or failed run is retried an hour later rather than costing the
entry. "Nothing exists" means all three of:

- `predictions/` on main has no `gwNN_*.csv`;
- no open PR adds one, whether this workflow opened it or a human did. One
  entry per (gameweek, model), RUNBOOK section 0;
- no `predict/gwNN` branch is left on the remote without a PR. That is a run
  that pushed and then failed, or a human halfway through an entry by hand.
  Either way it needs a person, so it fails rather than guessing. Branches
  are never deleted here, so a merged entry's branch stays; by then the entry
  is on main and the first check has already skipped.

It never fetches a snapshot, writes, or opens anything. It prints the decision,
adds it to the run's summary page, and writes `act`, `gameweek` and `deadline`
to `$GITHUB_OUTPUT` for the workflow's later steps.

An unreadable API is "could not check": it passes and says so, as the
watchdog does. The next hourly run tries again, and the watchdog is still the
alarm if no entry lands.

Usage, from the repository root:
    python tools/plan_entry.py --open-prs prs.json --remote-branches branches.txt
    python tools/plan_entry.py ... --ignore-window       # dry runs, any time
    python tools/plan_entry.py ... --fake-deadline-hours 6 --fake-gameweek 99

`prs.json` is `gh pr list --state open --json number,headRefName,files`.
`branches.txt` holds one remote branch name per line.
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from check_deadline import entries_for, fake_bootstrap, next_deadline

from fpl.fetch import get
from fpl.log import LOG_DIR_NAME, TIMESTAMP

# Late enough for the final press conferences before most deadlines, early
# enough that a failed run is retried a dozen times before the deadline.
WINDOW_HOURS = 12

ACT, SKIP, FAIL = "act", "skip", "fail"


def branch_for(gameweek):
    """The branch an entry for `gameweek` is made on, by hand or by the workflow."""
    return f"predict/gw{gameweek:02d}"


def prs_adding(gameweek, prs):
    """Open PRs that add an entry for `gameweek`, as (number, [entry names])."""
    found = []
    for pr in prs:
        names = [Path(f["path"]).name for f in pr.get("files", [])
                 if Path(f["path"]).parent.name == LOG_DIR_NAME]
        entries = entries_for(gameweek, names)
        if entries:
            found.append((pr["number"], entries))
    return found


def decide(bootstrap, names, prs, branches, now,
           window_hours=WINDOW_HOURS, ignore_window=False):
    """(ACT, SKIP or FAIL, gameweek or None, deadline or None, why)."""
    upcoming = next_deadline(bootstrap)
    if upcoming is None:
        return SKIP, None, None, "No next gameweek in bootstrap-static: nothing to enter."
    gameweek, deadline = upcoming
    hours = (deadline - now).total_seconds() / 3600
    when = f"GW{gameweek:02d} deadline {deadline.strftime(TIMESTAMP)}, {hours:.1f}h away"

    def result(status, why):
        return status, gameweek, deadline.strftime(TIMESTAMP), f"{when}. {why}"

    found = entries_for(gameweek, names)
    if found:
        return result(SKIP, f"Already in predictions/ on main: {', '.join(found)}.")
    pending = prs_adding(gameweek, prs)
    if pending:
        listed = "; ".join(f"#{n} ({', '.join(e)})" for n, e in pending)
        return result(SKIP, f"An open PR already adds an entry: {listed}. Merge that one.")
    branch = branch_for(gameweek)
    if branch in branches:
        return result(FAIL, f"Branch {branch} exists on the remote with no open PR adding "
                            "an entry. A run stopped after pushing, or an entry is being "
                            "made by hand. A person has to look: open the PR from it, "
                            "or make the entry by hand (RUNBOOK section 7).")
    if hours <= 0:
        return result(SKIP, "The deadline has passed. A missed deadline stays missed.")
    if hours > window_hours and not ignore_window:
        return result(SKIP, f"Over {window_hours}h away; a later run makes the entry.")
    return result(ACT, "Making the entry." + (" (window ignored)" if ignore_window else ""))


def report(status, why):
    """Print the decision, and add it to the run's summary page on GitHub."""
    line = f"{status.upper()}: {why}"
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"### Entry plan\n\n{line}\n")


def write_outputs(status, gameweek, deadline):
    """The step outputs the workflow branches on. Absent outside Actions."""
    outputs = os.environ.get("GITHUB_OUTPUT")
    if outputs:
        with open(outputs, "a") as f:
            f.write(f"act={'true' if status == ACT else 'false'}\n")
            f.write(f"gameweek={gameweek or ''}\n")
            f.write(f"deadline={deadline or ''}\n")


def main(predictions_dir, prs_file, branches_file, now=None, ignore_window=False,
         fake_hours=None, fake_gameweek=None):
    now = now or datetime.now(UTC)
    if fake_hours is not None:
        bootstrap = fake_bootstrap(fake_hours, fake_gameweek, now)
        print(f"FAKE deadline: GW{fake_gameweek:02d} in {fake_hours}h, not read from the API.")
    else:
        try:
            bootstrap = get("bootstrap-static/")
        except Exception as e:  # any failure to read is "could not check"
            report(SKIP, f"Could not check: bootstrap-static unreadable ({e}). "
                         "The next run tries again; the watchdog still warns.")
            write_outputs(SKIP, None, None)
            return 0
    directory = Path(predictions_dir)
    names = [p.name for p in directory.iterdir()] if directory.is_dir() else []
    prs = json.loads(Path(prs_file).read_text())
    branches = set(Path(branches_file).read_text().split())

    status, gameweek, deadline, why = decide(
        bootstrap, names, prs, branches, now, ignore_window=ignore_window)
    report(status, why)
    write_outputs(status, gameweek, deadline)
    return 1 if status == FAIL else 0


def cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--predictions", default=LOG_DIR_NAME, metavar="DIR",
                    help=f"the log directory on main (default: {LOG_DIR_NAME}/)")
    ap.add_argument("--open-prs", required=True, metavar="FILE",
                    help="gh pr list --state open --json number,headRefName,files")
    ap.add_argument("--remote-branches", required=True, metavar="FILE",
                    help="remote branch names, one per line")
    ap.add_argument("--ignore-window", action="store_true",
                    help="act whatever the time to the deadline (dry runs only)")
    ap.add_argument("--fake-deadline-hours", type=float, default=None, metavar="H",
                    help="skip the API: pretend the next deadline is H hours away")
    ap.add_argument("--fake-gameweek", type=int, default=None, metavar="N",
                    help="the gameweek of the fake deadline (with --fake-deadline-hours)")
    args = ap.parse_args(argv)
    if (args.fake_deadline_hours is None) != (args.fake_gameweek is None):
        ap.error("--fake-deadline-hours and --fake-gameweek go together.")
    return main(args.predictions, args.open_prs, args.remote_branches,
                ignore_window=args.ignore_window, fake_hours=args.fake_deadline_hours,
                fake_gameweek=args.fake_gameweek)


if __name__ == "__main__":
    sys.exit(cli())
