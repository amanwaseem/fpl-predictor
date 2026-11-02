"""Enforce hard rule 1: a committed prediction is never rewritten.

Compares every entry committed under `predictions/`, and under every later
season's `predictions-2027-28/` (#72), at a reference commit against the
working tree. A modified or deleted entry exits non-zero and is
named; a new entry passes, because appending is the whole point.

The rule was previously enforced by nothing but the "file already exists"
check inside `fpl/log/contract.py`, which stops a second *write* through that function.
It does nothing about an editor, a `sed -i`, or a rebase — the ways a
committed prediction actually gets altered. Those are exactly the changes a
human reviewer misses in a large diff, which is why CI runs this on every PR
and every push to main (`.github/workflows/ci.yml`).

`scores/` is deliberately not covered. It is derived output, recomputable from
the committed predictions plus a snapshot, and is meant to be rewritten when a
metric definition changes. Applying the rule there would freeze the metrics
instead of the evidence.

`--on-time` also refuses a new entry whose `deadline_utc` is less than
MERGE_MARGIN away, past, or unreadable (#119), unless a maintainer accepted it
in late_merges.json at its SHA-256: pushed before its deadline, merged after
(ADR 0026). Entry PRs merge themselves once
`ci` is green, so `ci` must not go green on a late entry: a re-run or a slow
queue would otherwise merge one with nobody looking. ci.yml runs it last, in
the `ci` job itself, which re-runs whenever any job does and goes green
seconds before the merge; the margin covers those seconds.

Usage:
    python tools/check_log_immutable.py
    python tools/check_log_immutable.py --ref origin/main
    python tools/check_log_immutable.py --ref origin/main --on-time
"""

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

LOG_DIR = "predictions"
# Every season's log directory: predictions/, then predictions-2027-28/ and
# on. A copy of fpl.log.contract's naming, since this runs without the
# package installed; tests/test_log_immutability.py checks the two agree.
LOG_DIRS = re.compile(r"^predictions(-\d{4}-\d{2})?$")
DEFAULT_REF = "origin/main"
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"
# How long before its deadline `ci` stops passing a new entry. Short, because
# the check runs at the end of `ci`, and the runbook's fallback can open an
# entry PR in the last quarter of an hour.
MERGE_MARGIN = timedelta(minutes=5)
# Entries a maintainer accepted although merged after their deadline, because
# each was pushed before it and is unchanged since (ADR 0026), by SHA-256.
LATE_MERGES = Path(__file__).with_name("late_merges.json")


def git(*args, root=None):
    """Run a git command, returning stdout as bytes."""
    result = subprocess.run(["git", *args], cwd=root, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def repo_root():
    try:
        return Path(git("rev-parse", "--show-toplevel").decode().strip())
    except RuntimeError as e:
        raise SystemExit(f"Not a git repository: {e}") from None


def log_dirs_at(ref, root):
    """Every season's log directory at `ref`, by name."""
    out = git("ls-tree", "-z", "-d", "--name-only", ref, root=root)
    return sorted(name for name in out.decode().split("\0") if LOG_DIRS.match(name))


def committed_entries(ref, root):
    """Paths under every season's log directory at `ref`, repo-relative."""
    dirs = log_dirs_at(ref, root)
    if not dirs:
        return []
    out = git("ls-tree", "-r", "-z", "--name-only", ref, "--", *dirs, root=root)
    return [p for p in out.decode().split("\0") if p]


def check(ref, root):
    """Return a list of rule 1 violations, empty if the log is intact."""
    try:
        git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", root=root)
    except RuntimeError:
        raise SystemExit(
            f"Cannot resolve {ref!r}.\n"
            "Nothing to compare against, so immutability cannot be verified. "
            "Fetch the reference first, or pass --ref.\n"
            "Failing closed: an unverifiable log is not a verified one."
        ) from None

    violations = []
    for path in committed_entries(ref, root):
        committed = git("show", f"{ref}:{path}", root=root)
        working = root / path
        if not working.exists():
            violations.append(f"{path}: deleted (committed at {ref})")
        elif working.read_bytes() != committed:
            violations.append(f"{path}: modified since {ref}")
    return violations


def added_entries(ref, root):
    """Entries in the working tree that are not at `ref`, repo-relative."""
    tracked = set(committed_entries(ref, root))
    return sorted(
        str(p.relative_to(root))
        for d in root.iterdir()
        if d.is_dir() and LOG_DIRS.match(d.name)
        for p in d.glob("*.csv")
        if str(p.relative_to(root)) not in tracked
    )


def entry_deadline(path):
    """The one deadline_utc on every row of an entry, or None if there is not one."""
    try:
        with open(path, newline="") as f:
            deadlines = {row["deadline_utc"] for row in csv.DictReader(f)}
        (deadline,) = deadlines
        return datetime.strptime(deadline, TIMESTAMP).replace(tzinfo=UTC)
    except (OSError, UnicodeDecodeError, csv.Error, KeyError, TypeError, ValueError):
        return None


def accepted_late(path, root, accepted):
    """Whether `path` is an accepted late merge, byte for byte as accepted."""
    record = accepted.get(path)
    if not isinstance(record, dict):
        return False
    return hashlib.sha256((root / path).read_bytes()).hexdigest() == record.get("sha256")


def too_late(paths, root, now=None, accepted=None):
    """The new entries under MERGE_MARGIN from their deadline or past it, with why.

    `accepted` is late_merges.json's records: those files pass at those hashes.
    """
    now = now or datetime.now(UTC)
    accepted = accepted if accepted is not None else json.loads(LATE_MERGES.read_text())
    late = []
    for path in paths:
        if accepted_late(path, root, accepted):
            print(f"  accepted late merge (ADR 0026, PR #{accepted[path].get('pr')}): {path}")
            continue
        deadline = entry_deadline(root / path)
        if deadline is None:
            late.append(f"{path}: no single readable deadline_utc, so it cannot be shown on time")
        elif now >= deadline:
            late.append(f"{path}: its deadline, {deadline:%Y-%m-%dT%H:%M:%SZ}, has passed")
        elif now >= deadline - MERGE_MARGIN:
            late.append(
                f"{path}: its deadline, {deadline:%Y-%m-%dT%H:%M:%SZ}, is under "
                f"{MERGE_MARGIN.seconds // 60} minutes away, too close to merge before it"
            )
    return late


def main(ref, on_time=False):
    root = repo_root()
    violations = check(ref, root)
    committed = committed_entries(ref, root)

    if violations:
        print(
            f"Hard rule 1 violated — {len(violations)} committed prediction(s) altered:\n",
            file=sys.stderr,
        )
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(
            "\nEntries in predictions/ are immutable once pushed. If a "
            "prediction was wrong it stays wrong and gets scored as wrong — "
            "the log is only evidence because it cannot be edited after the "
            f"fact.\nRestore them with: git checkout {ref} -- "
            + " ".join(f"{d}/" for d in log_dirs_at(ref, root)),
            file=sys.stderr,
        )
        return 1

    if not committed:
        print(
            f"No entries committed under {LOG_DIR}/ or a later season's at {ref} "
            "— nothing to protect yet."
        )
    else:
        print(
            f"{len(committed)} committed entr"
            f"{'y' if len(committed) == 1 else 'ies'} unchanged since {ref}."
        )

    # Additions are the normal case and are reported rather than counted as
    # violations: a new gameweek is exactly what appending to the log means.
    added = added_entries(ref, root)
    for path in added:
        print(f"  new entry (not yet at {ref}): {path}")

    if on_time:
        late = too_late(added, root)
        if late:
            print(
                f"Not mergeable before the deadline, {len(late)} new "
                f"entr{'y' if len(late) == 1 else 'ies'}:\n",
                file=sys.stderr,
            )
            for v in late:
                print(f"  {v}", file=sys.stderr)
            print(
                "\nAn entry merged after its deadline is not evidence of anything, "
                "and entry PRs merge themselves once ci is green, so ci refuses it. "
                "If the PR was opened before the deadline and the entry is unchanged, "
                "a maintainer can accept it in tools/late_merges.json (ADR 0026); "
                "otherwise close it, and the gameweek stays missed (ADR 0004).",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--ref", default=DEFAULT_REF, help=f"commit to compare against (default: {DEFAULT_REF})"
    )
    ap.add_argument(
        "--on-time",
        action="store_true",
        help=f"also refuse a new entry under {MERGE_MARGIN} from its deadline, or past it",
    )
    args = ap.parse_args()
    sys.exit(main(args.ref, args.on_time))
