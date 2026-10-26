"""One command for an entry: every logged model, from one snapshot (#47).

RUNBOOK steps 3-6, run for each model in LOGGED_MODELS:

    scratch run -> real write to predictions/ -> fpl-verify -> fpl-summarise

then a digest to paste into the PR body (RUNBOOK section 8). It calls each
command's own main(), so what it writes is what the manual steps write.

Two orderings differ from running the steps by hand, both so that a failure
leaves as little as possible in predictions/:

- Every model's scratch run comes first. Nothing is written to the log until
  each model has run cleanly from the snapshot.
- Each real write is compared with its scratch run, and must match it apart
  from generated_at_utc. Two runs from one snapshot that disagree mean the
  model is not deterministic. Both runs share one process, so agreeing does
  not prove the reverse: a model that depended on hash order, say, would agree
  with itself here and still differ in a fresh process.

It stops at the first failure and says which model and which step. It never
fetches, commits, pushes or opens a PR: a human reads the output first.

Usage:
    fpl-entry                         # every logged model, from data/raw/LATEST
    fpl-entry --snapshot 20261016T190000Z
    fpl-entry --dry-run               # the same, written under scratch/ instead
    fpl-entry --digest digest.md      # also copy the digest there

Run from the repository root.
"""

import argparse
import csv
import io
import sys
import tempfile
import traceback
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

from fpl.ingest.snapshot import load_snapshot, resolve_target_gw
from fpl.log import roles, summarise, verify_entry
from fpl.log.contract import TIMESTAMP
from fpl.models import gbm, predict_baseline, predict_fixture
from fpl.paths import require_project_root

# The models logged each gameweek, declared here and nowhere else. Adding a
# model to the log is adding it to this list, in the PR that freezes it.
LOGGED_MODELS = (
    (predict_baseline.MODEL_VERSION, predict_baseline.main),
    (predict_fixture.MODEL_VERSION, predict_fixture.main),
    # From GW9 (#85): its artifact is fetched by hash from its release.
    (gbm.MODEL_VERSION, gbm.main),
)


class StepFailed(Exception):
    def __init__(self, model, step, reason):
        super().__init__(f"{model}: {step} failed.\n{reason}")


class _Tee(io.TextIOBase):
    """Prints as usual, and keeps a copy for the digest."""

    def __init__(self, stream):
        self.stream = stream
        self.copy = io.StringIO()

    def write(self, text):
        self.stream.write(text)
        return self.copy.write(text)

    def flush(self):
        self.stream.flush()


def _run(model, step, fn, *args):
    """Run one step, returning what it printed. Raises StepFailed if it fails.

    A step fails by raising SystemExit (the predictors, load_snapshot) or by
    returning non-zero (fpl-verify, which reports every fault before it does).
    Anything else it raises is a failure too, with its traceback: main() still
    has to say which entries were left behind.
    """
    print(f"\n=== {model}: {step} ===")
    tee = _Tee(sys.stdout)
    try:
        with redirect_stdout(tee):
            code = fn(*args)
    except SystemExit as e:
        if e.code not in (None, 0):
            raise StepFailed(model, step, e.code) from None
        code = 0
    except Exception:
        raise StepFailed(model, step, traceback.format_exc()) from None
    if code:
        raise StepFailed(model, step, f"exited {code}; its output is above.")
    return tee.copy.getvalue()


def _rows_without_timestamp(path):
    with Path(path).open(newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        del row["generated_at_utc"]
    return rows


def _same_as_scratch(entry, check):
    if _rows_without_timestamp(entry) != _rows_without_timestamp(check):
        raise SystemExit(
            f"{entry} differs from the scratch run {check} beyond "
            "generated_at_utc.\nThe same model on the same snapshot gave two "
            "answers. Do not commit it."
        )
    return 0


def digest(snapshot_id, target_gw, deadline, outputs):
    """The PR body's record of the run: what RUNBOOK section 8 says to paste."""
    lines = [
        f"## GW{target_gw} entry",
        "",
        f"- snapshot: `{snapshot_id}`",
        f"- deadline: `{deadline}`",
        f"- models: {', '.join(f'`{m}`' for m in outputs)}",
        "",
        "Every model was run by `fpl-entry` from this one snapshot.",
    ]
    for model, (predicted, verified) in outputs.items():
        lines += [
            "",
            f"### {model}",
            "",
            "```",
            predicted.strip(),
            "```",
            "",
            "```",
            verified.strip(),
            "```",
        ]
    return "\n".join(lines) + "\n"


def main(snapshot_id=None, dry_run=False, models=LOGGED_MODELS, digest_out=None):
    # Checked first: discovering it after the entries are written would leave
    # them in predictions/ with no digest for the PR.
    if digest_out is not None and not Path(digest_out).parent.is_dir():
        raise SystemExit(
            f"--digest {digest_out}: {Path(digest_out).parent} does not exist. Nothing was run."
        )

    # A retired model's record has ended (fpl.log.roles); an entry for it
    # would start a second one under the same name.
    retired = [m for m, _ in models if m in roles.RETIRED]
    if retired:
        raise SystemExit(
            f"Retired, and not logged any more: {', '.join(retired)}. "
            "Take it out of LOGGED_MODELS. Nothing was run."
        )

    # Resolved once here, then passed to every model by id: each reads exactly
    # this snapshot, whatever LATEST says by the time it runs.
    snap, bootstrap, _, _ = load_snapshot(snapshot_id)
    snapshot_id = snap.name
    target_gw, deadline = resolve_target_gw(bootstrap["events"], None)

    # Both checked before anything runs, so a real run that cannot finish
    # stops here, not after every scratch run. A dry run only warns: it is how
    # a past gameweek gets replayed, and it should still say whether the real
    # run would be refused.
    names = [f"gw{target_gw:02d}_{m}.csv" for m, _ in models]
    taken = [str(Path("predictions") / n) for n in names if (Path("predictions") / n).exists()]
    if taken:
        refusal = (
            f"Already in the log: {', '.join(taken)}.\n"
            "fpl-entry writes every model or none. If a file there was "
            "never committed, it is from an earlier run that stopped: "
            "delete it and run again."
        )
        if not dry_run:
            raise SystemExit(refusal + "\nNothing was run.")
        print(f"WARNING: the real run would refuse. {refusal}\n")
    if datetime.now(UTC) >= datetime.strptime(deadline, TIMESTAMP).replace(tzinfo=UTC):
        late = f"The GW{target_gw} deadline {deadline} has passed."
        if not dry_run:
            raise SystemExit(
                late + " Nothing was run: an entry made now is not evidence of anything."
            )
        print(f"WARNING: {late} fpl-verify will refuse these entries.\n")

    # Its own directory per run, so the scratch runs never meet an earlier
    # run's files, which write_entry would refuse to overwrite.
    Path("scratch").mkdir(exist_ok=True)
    run_dir = Path(
        tempfile.mkdtemp(
            prefix=f"entry-gw{target_gw:02d}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-", dir="scratch"
        )
    )
    check_dir = run_dir / "check"
    # Not named predictions/ under scratch: log.write_entry treats any
    # directory of that name as the log, and would refuse a dry run past its
    # deadline.
    log_dir, reports_dir = (
        (run_dir / "entry", run_dir / "reports")
        if dry_run
        else (Path("predictions"), Path("reports"))
    )
    entries = {m: log_dir / n for (m, _), n in zip(models, names, strict=True)}

    print(f"snapshot:  {snapshot_id}")
    print(f"target:    GW{target_gw}")
    print(f"deadline:  {deadline}")
    print(f"models:    {', '.join(m for m, _ in models)}")
    print(f"writing:   {log_dir}/, {reports_dir}/" + ("  (dry run)" if dry_run else ""))

    outputs, reports = {}, []
    try:
        for model, predict in models:
            _run(model, "scratch run", predict, None, check_dir, snapshot_id)

        for model, predict in models:
            entry = entries[model]
            predicted = _run(model, "write", predict, None, log_dir, snapshot_id)
            _run(model, "same as the scratch run", _same_as_scratch, entry, check_dir / entry.name)
            verified = _run(model, "verify", verify_entry.main, entry)
            _run(model, "summarise", summarise.main, entry, reports_dir)
            reports.append(reports_dir / f"{entry.stem}.md")
            outputs[model] = (predicted, verified)
    except StepFailed as e:
        print(f"\nSTOPPED — {e}", file=sys.stderr)
        # None of the entries existed when the run started, so any that exist
        # now are this run's.
        written = [str(p) for p in [*entries.values(), *reports] if p.exists()]
        if written and not dry_run:
            print(
                "Written by this run but not committed: "
                + ", ".join(written)
                + "\nDo not commit a partial set. Fix the cause, delete these, "
                "and run again.",
                file=sys.stderr,
            )
        return 1

    text = digest(snapshot_id, target_gw, deadline, outputs)
    (run_dir / "digest.md").write_text(text)
    # For a caller that must not guess which scratch/entry-gw* directory is
    # this run's: the entry workflow puts this file in the PR body.
    if digest_out is not None:
        Path(digest_out).write_text(text)

    print("\n" + "=" * 72 + "\n")
    print(text)
    print(f"digest saved to {run_dir / 'digest.md'}")
    if dry_run:
        print("Dry run: nothing was written to predictions/ or reports/.")
    else:
        print("Nothing has been committed. Read the above, then RUNBOOK section 7.")
    return 0


def cli(argv=None):
    """`fpl-entry`, or `python -m fpl.log.entry`."""
    ap = argparse.ArgumentParser(
        description="Write, verify and summarise every logged model's entry from one snapshot."
    )
    ap.add_argument(
        "--snapshot",
        default=None,
        metavar="ID",
        help="snapshot id under data/raw/ (default: LATEST)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="write entries and reports under scratch/ instead of predictions/ and reports/",
    )
    ap.add_argument(
        "--digest",
        default=None,
        metavar="PATH",
        help="also write the digest to PATH, on success only",
    )
    args = ap.parse_args(argv)
    require_project_root()
    return main(args.snapshot, args.dry_run, digest_out=args.digest)


if __name__ == "__main__":
    sys.exit(cli())
