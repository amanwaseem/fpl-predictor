"""The prediction log contract: what a log entry contains and how it is written.

Hard rule 1 lives here. An entry is written once and never rewritten, so the
guards against overwriting one and against writing an empty one are part of
the contract rather than part of any model. The scoring harness reads these
same columns back, which is why the schema cannot live inside a model that is
meant to be replaced.
"""

import csv
import os
from datetime import datetime, timezone
from pathlib import Path

# The prediction log schema, in SPEC section 5 order. Declared rather than
# inferred from the first row: the column set is part of the log contract, and
# every gameweek has to stay comparable to the ones already committed.
FIELDS = [
    "gameweek", "player_id", "web_name", "team", "position", "price",
    "predicted_points", "expected_minutes", "points_per_90", "n_fixtures",
    "status", "model_version", "snapshot_id", "generated_at_utc", "deadline_utc",
]


def write_entry(rows, out_dir, target_gw, model_version, snapshot_id, deadline):
    """Write one prediction log entry and return its path.

    Stamps the provenance columns onto every row: which model produced the
    entry, which snapshot it was produced from, and when. A committed entry
    that cannot be traced back to its inputs is not evidence of anything.
    """
    if not rows:
        raise SystemExit(
            "No players produced a prediction — refusing to write an empty "
            "log entry.\nA header-only CSV in predictions/ would be permanent "
            "under hard rule 1 and indistinguishable from a real entry."
        )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    outdir = Path(out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    outpath = outdir / f"gw{target_gw:02d}_{model_version}.csv"

    taken = (
        f"{outpath} already exists.\n"
        "The prediction log is append-only — entries are never rewritten. "
        "Delete it manually only if it was never committed."
    )
    if outpath.exists():
        raise SystemExit(taken)

    # Build the entry in a temp file alongside the target, then link it into
    # place. A partial write is worse than no write here for a reason specific
    # to this directory: a truncated CSV sitting at the final path would be
    # refused by the append-only guard on the next run, so recovering from a
    # Ctrl-C would mean deleting a file out of predictions/ — the one operation
    # rule 1 exists to prevent, performed in a hurry.
    #
    # os.link rather than the os.replace that fetch.py uses. replace overwrites
    # by design, which is right for refetching a snapshot and wrong here; link
    # fails if the name is taken, so exclusivity and atomicity are a single
    # operation with no window between the check and the write.
    tmp = outpath.with_suffix(outpath.suffix + ".tmp")
    try:
        with tmp.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            for r in rows:
                r.update({
                    "model_version": model_version,
                    "snapshot_id": snapshot_id,
                    "generated_at_utc": generated_at,
                    "deadline_utc": deadline,
                })
                writer.writerow(r)
        try:
            os.link(tmp, outpath)
        except FileExistsError:
            raise SystemExit(taken)
    finally:
        # Runs on both paths: after a successful link the temp name is a second
        # name for the same file, and on failure it is a partial entry that must
        # not be left in predictions/ where it could be committed.
        tmp.unlink(missing_ok=True)

    return outpath
