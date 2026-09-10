"""Enforce hard rule 1: a committed prediction is never rewritten.

Compares every entry committed under `predictions/` at a reference commit
against the working tree. A modified or deleted entry exits non-zero and is
named; a new entry passes, because appending is the whole point.

The rule was previously enforced by nothing but the "file already exists"
check inside `fpl/log.py`, which stops a second *write* through that function.
It does nothing about an editor, a `sed -i`, or a rebase — the ways a
committed prediction actually gets altered. Those are exactly the changes a
human reviewer misses in a large diff, which is why this becomes a CI job
under #6.

`scores/` is deliberately not covered. It is derived output, recomputable from
the committed predictions plus a snapshot, and is meant to be rewritten when a
metric definition changes. Applying the rule there would freeze the metrics
instead of the evidence.

Usage:
    python tools/check_log_immutable.py
    python tools/check_log_immutable.py --ref origin/main
"""

import argparse
import subprocess
import sys
from pathlib import Path

LOG_DIR = "predictions"
DEFAULT_REF = "origin/main"


def git(*args, root=None):
    """Run a git command, returning stdout as bytes."""
    result = subprocess.run(
        ["git", *args], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def repo_root():
    try:
        return Path(git("rev-parse", "--show-toplevel").decode().strip())
    except RuntimeError as e:
        raise SystemExit(f"Not a git repository: {e}")


def committed_entries(ref, root):
    """Paths under predictions/ that exist at `ref`, repo-relative."""
    out = git("ls-tree", "-r", "-z", "--name-only", ref, "--", LOG_DIR, root=root)
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
        )

    violations = []
    for path in committed_entries(ref, root):
        committed = git("show", f"{ref}:{path}", root=root)
        working = root / path
        if not working.exists():
            violations.append(f"{path}: deleted (committed at {ref})")
        elif working.read_bytes() != committed:
            violations.append(f"{path}: modified since {ref}")
    return violations


def main(ref):
    root = repo_root()
    violations = check(ref, root)
    committed = committed_entries(ref, root)

    if violations:
        print(f"Hard rule 1 violated — {len(violations)} committed "
              f"prediction(s) altered:\n", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(
            "\nEntries in predictions/ are immutable once pushed. If a "
            "prediction was wrong it stays wrong and gets scored as wrong — "
            "the log is only evidence because it cannot be edited after the "
            f"fact.\nRestore them with: git checkout {ref} -- {LOG_DIR}/",
            file=sys.stderr,
        )
        return 1

    if not committed:
        print(f"No entries committed under {LOG_DIR}/ at {ref} — nothing to "
              "protect yet.")
    else:
        print(f"{len(committed)} committed entr"
              f"{'y' if len(committed) == 1 else 'ies'} unchanged since {ref}.")

    # Additions are the normal case and are reported rather than counted as
    # violations: a new gameweek is exactly what appending to the log means.
    tracked = set(committed)
    added = sorted(
        str(p.relative_to(root)) for p in (root / LOG_DIR).glob("*.csv")
        if str(p.relative_to(root)) not in tracked
    ) if (root / LOG_DIR).is_dir() else []
    for path in added:
        print(f"  new entry (not yet at {ref}): {path}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ref", default=DEFAULT_REF,
                    help=f"commit to compare against (default: {DEFAULT_REF})")
    args = ap.parse_args()
    sys.exit(main(args.ref))
