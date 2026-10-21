"""The entry workflow's decisions and checks, one subcommand per step.

GW5 and GW7 were missed the same way: nobody started the entry. The watchdog
(`tools/check_deadline.py`) warned before GW7 and nothing happened. The entry
workflow (`.github/workflows/entry.yml`, #68) runs hourly and calls this, so
the PR is already open when a human looks. Everything it decides is here,
where it can be tested; the workflow only runs commands and moves files.

Subcommands:

  decide          act, resume, skip or fail, and why (below).
  check-snapshot  the fresh snapshot is this run's, and predicts the planned
                  gameweek by the same rule fpl-entry uses.
  check-changes   the only changes are the planned gameweek's new entries,
                  each with its digest.
  commit-message  the entry commit's message, with the trailers that resume
                  and the scorecard read back.
  read-trailers   those trailers, from a commit message; fails without them.
  pr-body         the PR body: the deadline, the snapshot, what to check, the
                  digest.
  before-deadline fails once the deadline is under a margin away.
  token-expiry    fails when ENTRY_BOT_TOKEN has expired or is about to.

`decide` acts when the next deadline is WINDOW_HOURS away or less and has not
passed, and nothing for that gameweek exists yet:

- no entry on main for this season's gameweek. One from another season is
  not this deadline's entry: the log has no season dimension yet, so that
  fails for a person to look rather than skipping silently;
- no open PR adds one, whether this workflow opened it or a human did. One
  entry per (gameweek, model), RUNBOOK section 0;
- the workflow's own branch is not already there. If it is, with no PR, an
  earlier run pushed and then failed to open the PR: `resume` opens it from
  that branch rather than making a second entry. If a PR from it was closed
  without merging, a person closed it, and it is not reopened;
- no `predict/gwNN` branch, the name entries made by hand use, is left with
  no PR. Someone is halfway through an entry by hand.

Only inside the window does anything fail: a branch pushed by hand days
before the deadline must not turn every hourly run red. Branches are never
deleted, so the workflow's branch names carry the season and gameweek, and a
hand-made branch with a PR, merged or closed, is history and is ignored.

It also lists open PRs adding an entry for a gameweek whose deadline has
passed. Merging one would put a late entry in the log; the workflow comments
on each, once.

An unreadable API is "could not check": it skips and says so, as the watchdog
does. The next run tries again, and the watchdog is the alarm.

Usage, from the repository root:
    python tools/entry_bot.py decide --prs prs.json --branches branches.txt
    python tools/entry_bot.py decide ... --ignore-window       # dry runs
    python tools/entry_bot.py decide ... --fake-deadline-hours 6 --fake-gameweek 99

`prs.json` is `gh pr list --state all --json number,state,headRefName,files`.
`branches.txt` holds one remote branch name per line.
"""

import argparse
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from check_deadline import (
    Malformed,
    classify_entries,
    deadline_of,
    load_bootstrap,
    load_prs,
    next_deadline,
    prs_adding,
    season_of,
)

from fpl.log import LOG_DIR_NAME, TIMESTAMP, parse_entry_filename
from fpl.snapshot import SNAPSHOT_ID, load_snapshot, resolve_target_gw

# Late enough for the final press conferences before most deadlines, early
# enough that a failed run is retried a dozen times before the deadline. The
# watchdog's alarm (check_deadline.ALARM_HOURS) sits inside this.
WINDOW_HOURS = 12

# `gh pr list` is asked for this many. A list that long may be truncated, and
# a truncated list could hide the PR that already adds this week's entry.
PR_LIMIT = 500

ACT, RESUME, SKIP, FAIL = "act", "resume", "skip", "fail"

TRAILER_MADE_BY = "Entry-Made-By"
TRAILER_RELEASE = "Snapshot-Release"
TRAILER_SHA = "Snapshot-SHA256"
MADE_BY = "entry-automation"

# The comment left on a late PR, so a later run can tell it is already there.
LATE_MARKER = "<!-- entry-bot:late -->"


def bot_branch(season, gameweek):
    """The branch this workflow makes an entry on: season and gameweek both."""
    return f"entry-bot/{season}-gw{gameweek:02d}"


def hand_branch(gameweek):
    """The branch an entry made by hand goes on (RUNBOOK section 7)."""
    return f"predict/gw{gameweek:02d}"


def _prs_from(branch, prs):
    return [pr for pr in prs if pr.get("headRefName") == branch]


def late_prs(bootstrap, prs, now):
    """Open PRs adding an entry for a gameweek whose deadline has passed."""
    late = []
    for pr in prs:
        if pr.get("state", "OPEN") != "OPEN":
            continue
        gameweeks = set()
        for f in pr.get("files", []):
            path = Path(f["path"])
            if path.parent != Path(LOG_DIR_NAME):
                continue
            try:
                gameweeks.add(parse_entry_filename(path.name)[0])
            except ValueError:
                continue
        for gw in sorted(gameweeks):
            deadline = deadline_of(bootstrap, gw)
            if deadline is not None and deadline <= now:
                late.append((pr["number"], gw))
                break
    return late


def decide(bootstrap, predictions_dir, prs, branches, now,
           window_hours=WINDOW_HOURS, ignore_window=False):
    """A dict: action, gameweek, deadline, season, branch, late, why."""
    out = {"action": SKIP, "gameweek": None, "deadline": None, "season": None,
           "branch": None, "late": [], "why": ""}
    try:
        upcoming = next_deadline(bootstrap)
    except Malformed as e:
        out.update(action=FAIL, why=f"bootstrap-static is malformed ({e}). The API's "
                                    "schema may have changed. Nothing was made.")
        return out
    out["late"] = late_prs(bootstrap, prs, now)
    if upcoming is None:
        out["why"] = "No next gameweek in bootstrap-static: nothing to enter."
        return out

    gameweek, deadline = upcoming
    season = season_of(deadline)
    hours = (deadline - now).total_seconds() / 3600
    inside = 0 < hours and (hours <= window_hours or ignore_window)
    branch = bot_branch(season, gameweek)
    out.update(gameweek=gameweek, deadline=deadline.strftime(TIMESTAMP),
               season=season, branch=branch)
    when = (f"GW{gameweek:02d} ({season}) deadline {deadline.strftime(TIMESTAMP)}, "
            f"{hours:.1f}h away")

    def result(action, why):
        out.update(action=action, why=f"{when}. {why}")
        return out

    classified = classify_entries(gameweek, deadline, predictions_dir)
    entries = classified["current"] + classified["moved"]
    if entries:
        return result(SKIP, f"Already in predictions/ on main: {', '.join(entries)}.")
    pending = prs_adding(gameweek, prs)
    if pending:
        listed = "; ".join(f"#{n} ({', '.join(e)})" for n, e in pending)
        return result(SKIP, f"An open PR already adds an entry: {listed}. Merge that one.")
    if hours <= 0:
        return result(SKIP, "The deadline has passed. A missed deadline stays missed.")
    if not inside:
        return result(SKIP, f"Over {window_hours}h away; a later run makes the entry.")

    # Inside the window from here: a failure is worth an email.
    stale = classified["other_season"] + classified["unreadable"]
    if stale:
        return result(FAIL, f"predictions/ holds {', '.join(stale)}, for this gameweek "
                            "number but not this deadline (another season, or no readable "
                            "deadline_utc). Entry names carry no season, so a new entry "
                            "would collide with it. A person has to decide how the log "
                            "holds two seasons. Nothing was made.")

    if branch in branches:
        from_branch = _prs_from(branch, prs)
        if any(pr.get("state") == "OPEN" for pr in from_branch):
            numbers = ", ".join(f"#{pr['number']}" for pr in from_branch)
            return result(FAIL, f"{branch} has an open PR ({numbers}) that adds no entry "
                                "in predictions/. A person has to look.")
        if any(pr.get("state") == "MERGED" for pr in from_branch):
            return result(FAIL, f"A PR from {branch} was merged, yet main has no entry for "
                                "this deadline. Was it reverted? A person has to look.")
        if from_branch:
            numbers = ", ".join(f"#{pr['number']}" for pr in from_branch)
            return result(SKIP, f"The PR from {branch} ({numbers}) was closed without "
                                "merging, by a person. Not reopened. Make the entry by "
                                "hand if one is still wanted; the watchdog will warn.")
        return result(RESUME, f"{branch} exists with no PR: an earlier run pushed and "
                              "did not open it. Opening it from that branch.")

    hand = hand_branch(gameweek)
    if hand in branches and not _prs_from(hand, prs):
        return result(FAIL, f"Branch {hand} exists with no PR: an entry is being made by "
                            "hand. Not making a second. Open its PR, or make the entry "
                            "by hand (RUNBOOK section 7).")

    return result(ACT, "Making the entry." + (" (window ignored)" if ignore_window else ""))


def report(action, why, title="Entry workflow"):
    """Print the decision, and add it to the run's summary page on GitHub."""
    line = f"{action.upper()}: {why}"
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"### {title}\n\n{line}\n")


def write_outputs(**values):
    """Step outputs for the workflow. Ignored outside Actions."""
    outputs = os.environ.get("GITHUB_OUTPUT")
    if outputs:
        with open(outputs, "a") as f:
            for key, value in values.items():
                f.write(f"{key}={'' if value is None else value}\n")


def read_branches(path):
    names = Path(path).read_text().split()
    if not names:
        raise ValueError(f"{path}: no branches listed; main at least should be there")
    return set(names)


def cmd_decide(args, now=None):
    now = now or datetime.now(UTC)
    bootstrap, unreadable = load_bootstrap(now, args.fake_deadline_hours, args.fake_gameweek)
    if unreadable:
        report(SKIP, f"Could not check: {unreadable}. "
                     "The next run tries again; the watchdog still warns.")
        write_outputs(action=SKIP, gameweek=None, deadline=None, season=None,
                      branch=None, late="")
        return 0
    prs = load_prs(args.prs)
    if len(prs) >= PR_LIMIT:
        report(FAIL, f"{len(prs)} PRs listed: the list may be truncated, and could hide "
                     "the one that already adds this entry. Raise PR_LIMIT.")
        write_outputs(action=FAIL)
        return 1
    branches = read_branches(args.branches)
    out = decide(bootstrap, args.predictions, prs, branches, now,
                 ignore_window=args.ignore_window)
    report(out["action"], out["why"])
    for number, gw in out["late"]:
        print(f"LATE: #{number} adds a GW{gw:02d} entry, and that deadline has passed.")
    write_outputs(action=out["action"], gameweek=out["gameweek"], deadline=out["deadline"],
                  season=out["season"], branch=out["branch"],
                  late=" ".join(str(n) for n, _ in out["late"]))
    return 1 if out["action"] == FAIL else 0


def snapshot_problem(snapshot_id, gameweek, now, max_age_minutes):
    """Why the snapshot cannot be used for `gameweek`, or None if it can."""
    if not SNAPSHOT_ID.match(snapshot_id or ""):
        return f"{snapshot_id!r} is not a snapshot id."
    taken = datetime.strptime(snapshot_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    age = (now - taken).total_seconds() / 60
    if age > max_age_minutes:
        return (f"Snapshot {snapshot_id} is {age:.0f} minutes old; this run's fetch should "
                f"be under {max_age_minutes}. LATEST was not moved by this run.")
    if age < -5:
        return f"Snapshot {snapshot_id} is from the future: the runner's clock is wrong."
    try:
        _, bootstrap, _, _ = load_snapshot(snapshot_id)
        target, _ = resolve_target_gw(bootstrap["events"], None)
    except SystemExit as e:
        return f"Snapshot {snapshot_id} cannot be predicted from: {e}"
    if target != gameweek:
        return (f"Snapshot {snapshot_id} predicts GW{target}, not the planned GW{gameweek}. "
                "The deadline moved past while it was fetched; the next run plans again.")
    return None


def cmd_check_snapshot(args, now=None):
    problem = snapshot_problem(args.snapshot, args.gameweek, now or datetime.now(UTC),
                               args.max_age_minutes)
    print(problem or f"OK: snapshot {args.snapshot} is fresh and predicts GW{args.gameweek}.")
    return 1 if problem else 0


def unexpected_changes(lines, gameweek):
    """Problems with `git diff --name-status` lines for an entry, or [] if none."""
    gw = f"gw{gameweek:02d}"
    entry = re.compile(rf"^{LOG_DIR_NAME}/{gw}_([^_/]+)\.csv$")
    digest = re.compile(rf"^reports/{gw}_([^_/]+)\.md$")
    problems, models, digests = [], set(), set()
    for line in lines:
        if not line.strip():
            continue
        status, _, path = line.partition("\t")
        if status != "A":
            problems.append(f"{line.strip()}: only new files may change; this is {status!r}")
        elif m := entry.match(path):
            models.add(m.group(1))
        elif m := digest.match(path):
            digests.add(m.group(1))
        else:
            problems.append(f"{path}: not a GW{gameweek} entry or digest")
    if not models:
        problems.append(f"no {LOG_DIR_NAME}/{gw}_*.csv among the changes")
    for model in sorted(models - digests):
        problems.append(f"{gw}_{model}.csv has no reports/{gw}_{model}.md")
    for model in sorted(digests - models):
        problems.append(f"reports/{gw}_{model}.md has no entry beside it")
    return problems


def cmd_check_changes(args):
    problems = unexpected_changes(sys.stdin.read().splitlines(), args.gameweek)
    for problem in problems:
        print(f"UNEXPECTED: {problem}")
    if not problems:
        print(f"OK: only GW{args.gameweek} entries and their digests are added.")
    return 1 if problems else 0


def commit_message(gameweek, deadline, snapshot_id, tag, sha, models):
    joined = " and ".join(models) if len(models) < 3 else \
        ", ".join(models[:-1]) + " and " + models[-1]
    return (f"Log GW{gameweek} predictions: {joined}, from one snapshot\n\n"
            f"Made by the entry workflow from snapshot {snapshot_id}, before the\n"
            f"{deadline} deadline.\n\n"
            f"{TRAILER_MADE_BY}: {MADE_BY}\n"
            f"{TRAILER_RELEASE}: {tag}\n"
            f"{TRAILER_SHA}: {sha}\n")


def cmd_commit_message(args):
    sys.stdout.write(commit_message(args.gameweek, args.deadline, args.snapshot,
                                    args.tag, args.sha, args.models))
    return 0


def read_trailers(message):
    """{trailer: value} for this workflow's trailers. Raises ValueError if any is missing."""
    found = {}
    for line in message.splitlines():
        key, sep, value = line.partition(": ")
        if sep and key in (TRAILER_MADE_BY, TRAILER_RELEASE, TRAILER_SHA):
            found[key] = value.strip()
    if found.get(TRAILER_MADE_BY) != MADE_BY:
        raise ValueError(f"not made by the entry workflow: no '{TRAILER_MADE_BY}: {MADE_BY}'")
    for key in (TRAILER_RELEASE, TRAILER_SHA):
        if not found.get(key):
            raise ValueError(f"no {key} trailer")
    if not re.fullmatch(r"snapshot-\d{8}T\d{6}Z", found[TRAILER_RELEASE]):
        raise ValueError(f"{TRAILER_RELEASE} {found[TRAILER_RELEASE]!r} is not a snapshot tag")
    if not re.fullmatch(r"[0-9a-f]{64}", found[TRAILER_SHA]):
        raise ValueError(f"{TRAILER_SHA} {found[TRAILER_SHA]!r} is not a SHA-256")
    return found


def cmd_read_trailers(args):
    try:
        found = read_trailers(sys.stdin.read())
    except ValueError as e:
        print(f"REFUSED: {e}", file=sys.stderr if args.plain else sys.stdout)
        return 1
    tag = found[TRAILER_RELEASE]
    if args.plain:
        print(tag, found[TRAILER_SHA])
        return 0
    print(f"OK: {tag}, SHA-256 {found[TRAILER_SHA]}")
    write_outputs(tag=tag, sha=found[TRAILER_SHA], snapshot=tag.removeprefix("snapshot-"))
    return 0


def pr_body(gameweek, deadline, snapshot_id, tag, sha, release_url, run_url, digest,
            resumed=False):
    lines = [
        f"Made by the entry workflow (#68). **Merge before `{deadline}`.** "
        "A missed deadline stays missed, and a PR merged after it is a late entry.",
        "",
        f"- snapshot: `{snapshot_id}`, release asset [`{tag}`]({release_url}), "
        f"SHA-256 `{sha}`",
        f"- run: {run_url}",
    ]
    if resumed:
        lines.append("- opened by a later run: the run that made the entry pushed it "
                     "and could not open this PR. The entry was re-verified against "
                     "the release asset first.")
    lines += [
        "",
        'Before merging (RUNBOOK.md, "The automated entry"):',
        "",
        "- [ ] `included:` and `excluded:` below are what you expect: no unsettled "
        "round included",
        "- [ ] the deadline is right and `generated` is before it",
        "- [ ] `ci` is green",
        "",
        digest.strip(),
        "",
    ]
    return "\n".join(lines)


def cmd_pr_body(args):
    sys.stdout.write(pr_body(args.gameweek, args.deadline, args.snapshot, args.tag,
                             args.sha, args.release_url, args.run_url,
                             Path(args.digest).read_text(), args.resumed))
    return 0


def cmd_before_deadline(args, now=None):
    now = now or datetime.now(UTC)
    deadline = datetime.strptime(args.deadline, TIMESTAMP).replace(tzinfo=UTC)
    if now >= deadline - timedelta(minutes=args.margin_minutes):
        print(f"REFUSED: the {args.deadline} deadline is under {args.margin_minutes} "
              "minutes away or past. Nothing more is pushed or opened.")
        return 1
    print(f"OK: {(deadline - now).total_seconds() / 3600:.1f}h before the deadline.")
    return 0


EXPIRY_HEADER = "github-authentication-token-expiration"


def token_days_left(headers, now):
    """Days until the token expires, from `gh api -i` output, or None if it never does."""
    for line in headers.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == EXPIRY_HEADER:
            value = value.strip()
            for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%d %H:%M:%S %z"):
                try:
                    expires = datetime.strptime(value, fmt)
                except ValueError:
                    continue
                expires = expires.replace(tzinfo=UTC) if expires.tzinfo is None else expires
                return (expires - now).total_seconds() / 86400
            raise ValueError(f"unreadable {EXPIRY_HEADER}: {value!r}")
    return None


def cmd_token_expiry(args, now=None):
    now = now or datetime.now(UTC)
    text = sys.stdin.read()
    if not re.match(r"^HTTP/\S+ 200", text):
        status = text.splitlines()[0] if text else "no response"
        print(f"REFUSED: ENTRY_BOT_TOKEN was rejected ({status}). Create a new one: "
              'RUNBOOK.md, "The automated entry".')
        return 1
    try:
        days = token_days_left(text, now)
    except ValueError as e:
        print(f"REFUSED: {e}")
        return 1
    if days is None:
        print("OK: ENTRY_BOT_TOKEN does not expire.")
        return 0
    if days <= args.fail_days:
        print(f"REFUSED: ENTRY_BOT_TOKEN expires in {days:.1f} days. Replace it now: "
              'RUNBOOK.md, "The automated entry".')
        return 1
    print(f"OK: ENTRY_BOT_TOKEN expires in {days:.0f} days.")
    return 0


def cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("decide", help="act, resume, skip or fail")
    p.add_argument("--predictions", default=LOG_DIR_NAME, metavar="DIR")
    p.add_argument("--prs", required=True, metavar="FILE")
    p.add_argument("--branches", required=True, metavar="FILE")
    p.add_argument("--ignore-window", action="store_true",
                   help="act whatever the time to the deadline (dry runs only)")
    p.add_argument("--fake-deadline-hours", type=float, default=None, metavar="H")
    p.add_argument("--fake-gameweek", type=int, default=None, metavar="N")

    p = sub.add_parser("check-snapshot", help="the fresh snapshot predicts the plan's gameweek")
    p.add_argument("--snapshot", required=True)
    p.add_argument("--gameweek", type=int, required=True)
    p.add_argument("--max-age-minutes", type=int, default=60)

    p = sub.add_parser("check-changes", help="git diff --name-status on stdin")
    p.add_argument("--gameweek", type=int, required=True)

    p = sub.add_parser("commit-message")
    for name in ("--deadline", "--snapshot", "--tag", "--sha"):
        p.add_argument(name, required=True)
    p.add_argument("--gameweek", type=int, required=True)
    p.add_argument("--models", nargs="+", required=True)

    p = sub.add_parser("read-trailers", help="a commit message on stdin")
    p.add_argument("--plain", action="store_true",
                   help="print only '<tag> <sha>', for a shell to read")

    p = sub.add_parser("pr-body")
    for name in ("--deadline", "--snapshot", "--tag", "--sha", "--release-url",
                 "--run-url", "--digest"):
        p.add_argument(name, required=True)
    p.add_argument("--gameweek", type=int, required=True)
    p.add_argument("--resumed", action="store_true")

    p = sub.add_parser("before-deadline")
    p.add_argument("--deadline", required=True)
    p.add_argument("--margin-minutes", type=int, default=2)

    p = sub.add_parser("token-expiry", help="`gh api -i` output on stdin")
    p.add_argument("--fail-days", type=float, default=7)

    args = ap.parse_args(argv)
    if args.command == "decide" and \
            (args.fake_deadline_hours is None) != (args.fake_gameweek is None):
        ap.error("--fake-deadline-hours and --fake-gameweek go together.")
    return {
        "decide": cmd_decide,
        "check-snapshot": cmd_check_snapshot,
        "check-changes": cmd_check_changes,
        "commit-message": cmd_commit_message,
        "read-trailers": cmd_read_trailers,
        "pr-body": cmd_pr_body,
        "before-deadline": cmd_before_deadline,
        "token-expiry": cmd_token_expiry,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(cli())
