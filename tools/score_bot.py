"""The score workflow's decisions and checks, one subcommand per step (#162).

Entries are made and merged by the entry workflow (ADR 0024). Scoring them was
left to a person: notice a gameweek had settled, fetch, score, bring the
README into line, open a PR. When nobody did, the scorecard stopped while the
log went on. The score workflow (`.github/workflows/score.yml`) runs this;
everything it decides is here, where it can be tested.

Subcommands:

  decide            act, skip or fail, and why (below).
  entry-snapshots   the snapshot each newly scored gameweek's entries were made
                    from, with an entry that names it, for the workflow to fetch
                    from its release.
  check-snapshot    the fresh snapshot holds those gameweeks' final results and
                    was taken between gameweeks.
  check-changes     the only changes are scores/ and README.md, every score
                    file already there is unchanged but for the actuals id, and
                    every new one has its form comparators.
  named             the gameweeks as the titles name them: "GW9 and GW10".
  commit-message    the score commit's message, naming the actuals release.
  pr-body           the PR body: each new gameweek's results, the snapshot.

`decide` acts when a gameweek in the season's log has an entry with no score
file, that gameweek is settled (finished and data_checked), and the latest
gameweek to start is settled too. That last condition is what keeps the
actuals snapshot between gameweeks: a snapshot mid-gameweek has zeroed rows for
fixtures not yet played (CLAUDE.md). It skips, rather than acts, when:

- a score-bot PR is open: the workflow arms its auto-merge;
- an open PR already changes the season's scores/, whoever opened it;
- an open PR adds an entry for a gameweek about to be scored, such as one
  waiting to be accepted late (ADR 0026): scored once it merges or closes.

Branches are never deleted, so a gameweek scored again (an entry accepted
after it was first scored) takes the next free name, -2, -3. It fails for a
person to look when a branch of its has no open or merged PR: a closed one was
a veto, and the gameweek is scored by hand (docs/runbook.md section 10).

An unreadable API is "could not check": it skips, and the next run tries again.

Usage, from the repository root:
    python tools/score_bot.py decide --prs prs.json --branches branches.txt
"""

import argparse
import csv
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from check_deadline import load_bootstrap, load_prs
from entry_bot import PR_LIMIT, open_count, read_branches, report, write_outputs

from fpl.ingest.snapshot import load_snapshot
from fpl.log.contract import (
    SCORES,
    parse_entry_filename,
    parse_timestamp,
    season_directory,
    season_of_deadline,
)

ACT, SKIP, FAIL = "act", "skip", "fail"

# A score PR open this long has not merged itself: ci is red, or auto-merge
# was never armed. Skipping behind it would stall scoring with every run
# green, so from then on each run fails, and GitHub emails.
STUCK_HOURS = 24
README = "README.md"
TITLE = "Score workflow"


def bot_branch(season, gameweek):
    """The branch the workflow scores on: named by season and newest gameweek."""
    return f"score-bot/{season}-gw{gameweek:02d}"


def settled(event):
    return bool(event.get("finished") and event.get("data_checked"))


def latest_started(events, now):
    """The event with the latest deadline that has passed, or None before the season."""
    started = [e for e in events if parse_timestamp(e["deadline_time"], "deadline") <= now]
    return max(started, key=lambda e: e["deadline_time"]) if started else None


def unscored(log_dir, scores_dir):
    """({gameweek: [model, ...]} for every entry with no score file,
    [files in the log that are not entries])."""
    found: dict[int, list[str]] = {}
    strays = []
    for path in sorted(Path(log_dir).glob("*.csv")):
        try:
            gameweek, model = parse_entry_filename(path)
        except ValueError:
            strays.append(path.name)
            continue
        if not (Path(scores_dir) / f"{path.stem}.json").exists():
            found.setdefault(gameweek, []).append(model)
    return found, strays


def entry_gameweeks(paths, log_dir):
    """The gameweeks of the entries among `paths` in `log_dir`."""
    found = []
    for path in paths:
        if Path(path).parent == Path(log_dir) and path.endswith(".csv"):
            try:
                found.append(parse_entry_filename(path)[0])
            except ValueError:
                continue
    return found


def decide(bootstrap, prs, branches, now, root="."):
    """{"action", "why", "gameweeks", "season", "branch", "log_dir", "scores_dir"}."""
    out: dict[str, Any] = {
        "gameweeks": [],
        "season": None,
        "branch": None,
        "log_dir": None,
        "scores_dir": None,
    }

    def result(action, why):
        return {**out, "action": action, "why": why}

    events = bootstrap.get("events") or []
    current = latest_started(events, now)
    if current is None:
        return result(SKIP, "No gameweek has started yet.")
    if not settled(current):
        return result(
            SKIP,
            f"GW{current['id']} is under way or its results are not final: a snapshot now "
            "would not be between gameweeks.",
        )

    season = season_of_deadline(current["deadline_time"])
    log_dir = season_directory(season)
    scores_dir = season_directory(season, SCORES)
    done = {e["id"] for e in events if settled(e)}
    waiting, strays = unscored(Path(root) / log_dir, Path(root) / scores_dir)
    if strays:
        return result(
            FAIL,
            f"{log_dir}/ holds {', '.join(strays)}, which are not entries; fpl-score refuses "
            "that. A person has to look.",
        )
    ready = sorted(gw for gw in waiting if gw in done)
    out.update(season=season.replace("/", "-"), log_dir=str(log_dir), scores_dir=str(scores_dir))
    if not ready:
        return result(SKIP, "Every settled gameweek in the log is scored.")

    out.update(gameweeks=ready)
    gameweeks = named(ready)

    if open_count(prs) >= PR_LIMIT:
        return result(
            FAIL, f"{open_count(prs)} open PRs listed: the list may be truncated. Raise PR_LIMIT."
        )
    for pr in prs:
        if pr.get("state") != "OPEN":
            continue
        paths = [f.get("path", "") for f in pr.get("files") or []]
        if str(pr.get("headRefName", "")).startswith("score-bot/"):
            opened = pr.get("createdAt")
            age = (
                (now - parse_timestamp(opened, "createdAt")).total_seconds() / 3600 if opened else 0
            )
            if age > STUCK_HOURS:
                return result(
                    FAIL,
                    f"#{pr['number']} has been open {age:.0f} hours without merging itself: its "
                    "ci is red, or auto-merge is off. A person has to look.",
                )
            return result(SKIP, f"#{pr['number']} is already open to score {gameweeks}.")
        if any(p.startswith(f"{scores_dir}/") for p in paths):
            return result(SKIP, f"#{pr['number']} already changes {scores_dir}/; it goes first.")
        adding = sorted(set(entry_gameweeks(paths, log_dir)) & set(ready))
        if adding:
            # Scoring now would leave that entry to a second run, and a late
            # one may yet be accepted (ADR 0026): wait until it merges or closes.
            return result(
                SKIP,
                f"#{pr['number']} adds a {named(adding)} entry; scored once it merges or closes.",
            )

    # Branches are never deleted, so a gameweek scored twice (an entry accepted
    # after its gameweek was first scored) takes the next free name. A branch
    # whose PR merged is history; any other is for a person to look at.
    base = bot_branch(out["season"], ready[-1])
    for n in range(1, 100):
        branch = base if n == 1 else f"{base}-{n}"
        if branch not in branches:
            break
        if not any(pr.get("state") == "MERGED" for pr in prs if pr.get("headRefName") == branch):
            return result(
                FAIL,
                f"{branch} exists with no open or merged PR: a run failed after pushing, or its "
                f"PR was closed. A person has to look; score {gameweeks} by hand "
                "(docs/runbook.md section 10).",
            )
    out.update(branch=branch)
    return result(ACT, f"Scoring {gameweeks}.")


def cmd_decide(args, now=None):
    now = now or datetime.now(UTC)
    bootstrap, unreadable = load_bootstrap(now)
    if unreadable:
        report(SKIP, f"Could not check: {unreadable}. The next run tries again.", TITLE)
        write_outputs(action=SKIP)
        return 0
    out = decide(bootstrap, load_prs(args.prs), read_branches(args.branches), now)
    report(out["action"], out["why"], TITLE)
    write_outputs(
        action=out["action"],
        gameweeks=" ".join(str(gw) for gw in out["gameweeks"]),
        season=out["season"],
        branch=out["branch"],
        log_dir=out["log_dir"],
        scores_dir=out["scores_dir"],
    )
    return 1 if out["action"] == FAIL else 0


def entry_snapshots(log_dir, gameweeks):
    """{snapshot_id: an entry made from it} for the entries of `gameweeks`."""
    found: dict[str, str] = {}
    for path in sorted(Path(log_dir).glob("*.csv")):
        if parse_entry_filename(path)[0] not in gameweeks:
            continue
        with path.open(newline="") as f:
            ids = {row["snapshot_id"] for row in csv.DictReader(f)}
        if len(ids) != 1:
            raise ValueError(f"{path} names {len(ids)} snapshots; fpl-verify refuses that")
        found.setdefault(ids.pop(), str(path))
    return found


def cmd_entry_snapshots(args):
    for snapshot_id, path in entry_snapshots(args.log_dir, set(args.gameweeks)).items():
        print(snapshot_id, path)
    return 0


def snapshot_problem(bootstrap, gameweeks, now):
    """Why this snapshot cannot be the actuals for `gameweeks`, or None."""
    events = {e["id"]: e for e in bootstrap.get("events") or []}
    for gw in gameweeks:
        if gw not in events or not settled(events[gw]):
            return f"GW{gw} is not finished and data_checked in it"
    current = latest_started(list(events.values()), now)
    if current is not None and not settled(current):
        return f"GW{current['id']} had started and was not settled: not between gameweeks"
    return None


def cmd_check_snapshot(args, now=None):
    _, bootstrap, _, _ = load_snapshot(args.snapshot)
    problem = snapshot_problem(bootstrap, args.gameweeks, now or datetime.now(UTC))
    if problem:
        print(f"REFUSED: snapshot {args.snapshot}: {problem}.")
        return 1
    print(f"OK: snapshot {args.snapshot} holds final results, between gameweeks.")
    return 0


def cumulative_problems(before, after, gameweeks):
    """Ways cumulative.json's per-gameweek figures moved for a gameweek not being scored.

    The pooled figures move with every new gameweek; each earlier gameweek's
    own line must not.
    """
    problems = []
    for model, block in before.get("models", {}).items():
        new_block = after.get("models", {}).get(model)
        if new_block is None:
            problems.append(f"cumulative.json: {model} is gone")
            continue
        old = {g["gameweek"]: g for g in block.get("per_gameweek", [])}
        new = {g["gameweek"]: g for g in new_block.get("per_gameweek", [])}
        for gw, line in old.items():
            if gw not in gameweeks and new.get(gw) != line:
                problems.append(f"cumulative.json: {model}'s GW{gw} figures changed")
    return problems


def comparable(text, rescored):
    """A score file as compared before and after: without its actuals id, and,
    for a gameweek being rescored, without its head-to-head against the
    baseline, which a late baseline entry adds."""
    data = json.loads(text)
    data.pop("actuals_snapshot_id", None)
    if rescored:
        data.pop("head_to_head", None)
    return data


def change_problems(lines, gameweeks, scores_dir, show):
    """Every way `git diff --name-status` lines go beyond a scoring run.

    `show(path)` returns the file as it was before. A score file that was there
    already may change only in its actuals_snapshot_id, so a gameweek rescored
    for an entry accepted late keeps its other models' scores. Every new score
    file must have its form comparators: without them, its entry's snapshot
    was not fetched.
    """
    problems = []
    new_names = tuple(f"gw{gw:02d}_" for gw in gameweeks)
    added = 0
    for line in lines:
        if not line.strip():
            continue
        status, path = line.split("\t", 1)
        name = Path(path).name
        if path == README:
            if status != "M":
                problems.append(f"{status} {path}: README.md may only be modified")
        elif Path(path).parent != Path(scores_dir):
            problems.append(f"{status} {path}: outside {scores_dir}/ and README.md")
        elif name == "cumulative.json":
            # Added in a season's first scoring run; modified in every other.
            if status == "M":
                before, after = json.loads(show(path)), json.loads(Path(path).read_text())
                problems += cumulative_problems(before, after, gameweeks)
            elif status != "A":
                problems.append(f"{status} {path}: cumulative.json may only change")
        elif status == "A":
            if not name.startswith(new_names):
                problems.append(f"A {path}: a score file for a gameweek not being scored")
            elif name.endswith(".json"):
                added += 1
                problems += comparator_problems([json.loads(Path(path).read_text())])
        elif status == "M" and name.endswith(".json"):
            rescored = name.startswith(new_names)
            if comparable(show(path), rescored) != comparable(Path(path).read_text(), rescored):
                problems.append(f"M {path}: changed beyond its actuals_snapshot_id")
        else:
            problems.append(f"{status} {path}: a score file already there changed")
    if not added:
        problems.append(f"no new score file for {named(sorted(gameweeks))}")
    return problems


def git_show(path):
    return subprocess.run(
        ["git", "show", f"HEAD:{path}"], check=True, capture_output=True, text=True
    ).stdout


def cmd_check_changes(args):
    lines = sys.stdin.read().splitlines()
    problems = change_problems(lines, set(args.gameweeks), args.scores_dir, git_show)
    for p in problems:
        print(f"REFUSED: {p}")
    if not problems:
        print(
            f"OK: only {named(args.gameweeks)}'s scores are new, with comparators; "
            "the rest are unchanged."
        )
    return 1 if problems else 0


def new_scores(scores_dir, gameweeks):
    """The score files of `gameweeks`, by gameweek then model."""
    found = []
    for path in sorted(Path(scores_dir).glob("gw*_*.json")):
        data = json.loads(path.read_text())
        if data["gameweek"] in gameweeks:
            found.append(data)
    return found


def comparator_problems(scores):
    """Score files with no comparators: their entry's snapshot was missing."""
    return [
        f"GW{d['gameweek']} {d['model_version']} has no comparators: its entry's snapshot "
        f"{d['prediction_snapshot_id']} was not fetched"
        for d in scores
        if not d["comparators"].get("available")
    ]


def named(gameweeks):
    gws = [f"GW{gw}" for gw in gameweeks]
    return gws[0] if len(gws) == 1 else ", ".join(gws[:-1]) + " and " + gws[-1]


def results_table(scores):
    """The PR's table: the README's per-gameweek rows for the new gameweeks."""
    from readme_results import PER_GAMEWEEK_HEADER, per_gameweek_rows

    return "\n".join([*PER_GAMEWEEK_HEADER, *per_gameweek_rows(scores)])


def commit_message(gameweeks, snapshot_id, tag, sha):
    return (
        f"Score {named(gameweeks)}, against a snapshot taken after it settled\n\n"
        f"Made by the score workflow. Snapshot {snapshot_id}, taken between\n"
        "gameweeks, holds the actuals; fpl-score rescored every entry against it,\n"
        "and tools/readme_results.py brought both README results tables into line.\n"
        "Earlier gameweeks' scores are unchanged but for actuals_snapshot_id.\n\n"
        f"Actuals-Release: {tag}\n"
        f"Actuals-SHA256: {sha}\n"
    )


def cmd_commit_message(args):
    sys.stdout.write(commit_message(args.gameweeks, args.snapshot, args.tag, args.sha))
    return 0


def pr_body(gameweeks, snapshot_id, tag, sha, release_url, run_url, scores):
    return (
        f"Made by the [score workflow]({run_url}) (#162). {named(gameweeks)} "
        f"{'is' if len(gameweeks) == 1 else 'are'} finished and `data_checked`; "
        f"snapshot `{snapshot_id}`, taken between gameweeks, holds the actuals. It is the "
        f"release [`{tag}`]({release_url}), SHA-256 `{sha}`.\n\n"
        f"{results_table(scores)}\n\n"
        "Checked before it was pushed:\n\n"
        "- Only `scores/` and `README.md` change, and nothing in `predictions/`.\n"
        "- Every gameweek scored before is unchanged but for `actuals_snapshot_id`.\n"
        "- Every new score file has its `form` comparators, from its entry's snapshot.\n"
        "- Both README results tables follow `scores/`, checked by "
        "`tests/project/test_readme.py`.\n\n"
        "This merges itself once `ci` is green. Closing it is the veto; the gameweek is "
        "then scored by hand (docs/runbook.md section 10).\n"
    )


def cmd_pr_body(args):
    scores = new_scores(args.scores_dir, set(args.gameweeks))
    sys.stdout.write(
        pr_body(
            args.gameweeks,
            args.snapshot,
            args.tag,
            args.sha,
            args.release_url,
            args.run_url,
            scores,
        )
    )
    return 0


def cmd_named(args):
    print(named(args.gameweeks))
    return 0


def cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("decide", help="act, skip or fail")
    p.add_argument("--prs", required=True, metavar="FILE")
    p.add_argument("--branches", required=True, metavar="FILE")

    p = sub.add_parser("entry-snapshots", help="'<snapshot id> <entry>' per line")
    p.add_argument("--log-dir", required=True)
    p.add_argument("--gameweeks", type=int, nargs="+", required=True)

    p = sub.add_parser("check-snapshot")
    p.add_argument("--snapshot", required=True)
    p.add_argument("--gameweeks", type=int, nargs="+", required=True)

    p = sub.add_parser("check-changes", help="git diff --name-status on stdin")
    p.add_argument("--scores-dir", required=True)
    p.add_argument("--gameweeks", type=int, nargs="+", required=True)

    p = sub.add_parser("named")
    p.add_argument("--gameweeks", type=int, nargs="+", required=True)

    p = sub.add_parser("commit-message")
    for name in ("--snapshot", "--tag", "--sha"):
        p.add_argument(name, required=True)
    p.add_argument("--gameweeks", type=int, nargs="+", required=True)

    p = sub.add_parser("pr-body")
    for name in ("--snapshot", "--tag", "--sha", "--release-url", "--run-url", "--scores-dir"):
        p.add_argument(name, required=True)
    p.add_argument("--gameweeks", type=int, nargs="+", required=True)

    args = ap.parse_args(argv)
    return {
        "decide": cmd_decide,
        "entry-snapshots": cmd_entry_snapshots,
        "check-snapshot": cmd_check_snapshot,
        "check-changes": cmd_check_changes,
        "named": cmd_named,
        "commit-message": cmd_commit_message,
        "pr-body": cmd_pr_body,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(cli())
