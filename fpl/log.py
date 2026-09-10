"""The prediction log contract: what a log entry contains and how it is written.

Hard rule 1 lives here. An entry is written once and never rewritten, so the
guards against overwriting one and against writing an empty one are part of
the contract rather than part of any model. The scoring harness reads these
same columns back, which is why the schema cannot live inside a model that is
meant to be replaced.

Validation happens at write time, before the deadline, because every check
that runs before a deadline is recoverable and every check that runs after one
is not. A malformed entry discovered at scoring time is discovered after the
file has become immutable.
"""

import csv
import math
import os
import re
from collections import Counter
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

# Stamped by write_entry from its own arguments rather than supplied by the
# model. A caller that sets these itself is either confused or forging
# provenance, so validate_rows treats them like any other unexpected key.
PROVENANCE = ["model_version", "snapshot_id", "generated_at_utc", "deadline_utc"]

# SPEC section 5: availability flag at prediction time.
STATUSES = frozenset("adisun")

# SPEC section 5: the four FPL positions. Validated because
# predict_baseline maps element_type through a dict with a "UNK" fallback, and
# FPL has added an element_type mid-era before now — managers, in 2024/25. A
# new one would otherwise write "UNK" into a permanent entry with nothing
# flagging it until the position breakdown in the scoring harness came out
# wrong weeks later.
POSITIONS = frozenset({"GKP", "DEF", "MID", "FWD"})

# Columns SPEC section 5 types as int. Checked separately from NUMERIC because
# a float that happens to be integral still reaches the CSV as "1.0", and the
# scoring harness joins on player_id.
INTEGER = ["gameweek", "player_id", "n_fixtures"]

# Columns that must hold a real number. predicted_points and points_per_90 are
# absent from NON_NEGATIVE on purpose: a red card or an own goal makes a
# genuinely negative FPL score, and a model predicting one must be able to say
# so rather than have the log silently refuse it.
NUMERIC = [
    "gameweek", "player_id", "price", "predicted_points",
    "expected_minutes", "points_per_90", "n_fixtures",
]
NON_NEGATIVE = ["gameweek", "player_id", "price", "expected_minutes", "n_fixtures"]

TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"

# The one directory carrying SPEC section 5 rules 1 and 2.
LOG_DIR_NAME = "predictions"

# SPEC section 5: gw<NN>_<model_version>.csv, zero-padded, and model_version
# free of "_" or the name has two readings.
ENTRY_FILENAME = re.compile(r"^gw(\d{2})_([^_]+)\.csv$")

# A systematically broken entry has one fault per row. Reporting all 654 of
# them buries the diagnosis; reporting one at a time means finding the next
# only after fixing the last, at a deadline.
MAX_REPORTED = 10


def parse_entry_filename(path):
    """Split an entry filename back into (gameweek, model_version).

    The inverse of the name write_entry builds. The scoring harness reads
    `predictions/` as a directory listing, so the filename has to be
    unambiguously parseable — which is why `model_version` may not contain an
    underscore, and why the gameweek is zero-padded rather than bare.

    Raises ValueError rather than exiting: callers that walk a directory want
    to report a bad name alongside the others, not die on the first one.
    """
    name = Path(path).name
    match = ENTRY_FILENAME.match(name)
    if match is None:
        raise ValueError(
            f"{name!r} is not a prediction log entry name.\n"
            "Expected gw<NN>_<model_version>.csv with a zero-padded gameweek "
            "and no underscore in the model version — 'gw04_baseline-v1.csv', "
            "not 'gw4_baseline-v1.csv' and not 'gw04_baseline_v1.csv'."
        )
    return int(match.group(1)), match.group(2)


def _number_fault(row, index, field):
    """Why `field` is not a usable number in this row, or None if it is."""
    value = row[field]
    # bool is a subclass of int, so True would otherwise pass as a number and
    # then be written to the CSV as "True".
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"row {index}: {field} is {value!r}, which is not a number"
    if math.isnan(value):
        return f"row {index}: {field} is NaN"
    if math.isinf(value):
        return f"row {index}: {field} is {value}"
    if field in NON_NEGATIVE and value < 0:
        return f"row {index}: {field} is {value}, which cannot be negative"
    if field in INTEGER and not isinstance(value, int):
        return (f"row {index}: {field} is {value!r}, which SPEC section 5 types "
                "as int — a float reaches the CSV as '1.0'")
    return None


def validate_rows(rows, target_gw):
    """Check a complete entry against the SPEC section 5 schema.

    Expects rows with every column present and typed — the shape write_entry
    holds just before it writes, and the shape the scoring harness will read
    back. Raises ValueError listing what is wrong; returns None if the entry
    is sound.

    Collects faults rather than raising on the first, because the caller is
    usually a person minutes from a deadline who needs to know how much is
    wrong, not merely that something is.
    """
    if not rows:
        raise ValueError("an entry must contain at least one row")

    faults = []
    expected = set(FIELDS)
    well_formed = True

    for index, row in enumerate(rows):
        missing = expected - set(row)
        unexpected = set(row) - expected
        if missing:
            faults.append(f"row {index}: missing {sorted(missing)}")
        if unexpected:
            faults.append(f"row {index}: unexpected {sorted(unexpected)}")
        if missing or unexpected:
            # The per-field checks below would raise KeyError, and their
            # findings would be noise next to a wrong column set anyway.
            well_formed = False
            continue

        if row["gameweek"] != target_gw:
            faults.append(
                f"row {index}: gameweek is {row['gameweek']!r}, "
                f"but this entry targets GW{target_gw}"
            )
        if row["status"] not in STATUSES:
            faults.append(
                f"row {index}: status is {row['status']!r}, "
                f"not one of {' '.join(sorted(STATUSES))}"
            )
        if row["position"] not in POSITIONS:
            faults.append(
                f"row {index}: position is {row['position']!r}, "
                f"not one of {' '.join(sorted(POSITIONS))}"
            )
        for field in NUMERIC:
            fault = _number_fault(row, index, field)
            if fault:
                faults.append(fault)
                well_formed = False

    # One row per player. Duplicates pass the sort-order check trivially — a
    # repeated id is already in ascending order with itself — and would be
    # double-counted by every metric the scoring harness pools, with nothing in
    # the committed file revealing it.
    counts = Counter(row["player_id"] for row in rows if "player_id" in row)
    duplicated = sorted((pid for pid, n in counts.items() if n > 1), key=str)
    if duplicated:
        faults.append(
            f"player_id appears more than once: {duplicated[:5]}"
            + (f" and {len(duplicated) - 5} more" if len(duplicated) > 5 else "")
        )

    # SPEC section 5: descending predicted_points, ties by ascending player_id.
    # Skipped when the numbers are already suspect, since sorting NaN or a
    # string would report a second, derived failure for the same cause.
    if well_formed:
        order = [(-row["predicted_points"], row["player_id"]) for row in rows]
        if order != sorted(order):
            first = next(i for i in range(len(order) - 1)
                         if order[i] > order[i + 1])
            faults.append(
                f"rows {first} and {first + 1} are out of the contract sort "
                "order (descending predicted_points, ties by ascending "
                "player_id): "
                f"{rows[first]['predicted_points']}/"
                f"{rows[first]['player_id']} before "
                f"{rows[first + 1]['predicted_points']}/"
                f"{rows[first + 1]['player_id']}"
            )

    if faults:
        shown = faults[:MAX_REPORTED]
        more = (f"\n... and {len(faults) - MAX_REPORTED} more"
                if len(faults) > MAX_REPORTED else "")
        raise ValueError(
            f"{len(faults)} schema violation(s) in this entry:\n  "
            + "\n  ".join(shown) + more
        )


def _parse_timestamp(value, what):
    try:
        return datetime.strptime(value, TIMESTAMP).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise SystemExit(
            f"{what} is {value!r}, which is not a SPEC section 5 timestamp.\n"
            "Expected YYYY-MM-DDTHH:MM:SSZ."
        )


def _is_the_log(out_dir):
    """Whether this directory is the append-only log rather than scratch space.

    Only `predictions/` carries hard rules 1 and 2. Exploratory runs against a
    past gameweek are legitimate and must stay possible, so the post-deadline
    guard is fatal here and advisory everywhere else.

    Matched on the directory's own name rather than on its path relative to the
    process working directory. This module has no reliable notion of the
    repository root — #5 has yet to settle how paths are anchored — and
    resolving "predictions" against the cwd made a deadline-critical guard fail
    open for the very same directory reached from anywhere else, degrading a
    refusal into a warning that scrolls past above twenty lines of table.

    Fails closed by design: scratch space that happens to be named predictions
    gets the strict treatment, which costs a rename. The other direction costs
    a committed entry that cannot be told from one written knowing the result.
    """
    return Path(out_dir).resolve().name == LOG_DIR_NAME


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

    if "_" in model_version:
        raise SystemExit(
            f"model_version {model_version!r} contains an underscore.\n"
            f"'gw{target_gw:02d}_{model_version}.csv' would have two readings "
            "and stop being parseable back into gameweek and model version.\n"
            "Use '-' instead."
        )

    deadline_at = _parse_timestamp(deadline, "deadline")
    now = datetime.now(timezone.utc)
    generated_at = now.strftime(TIMESTAMP)

    # SPEC section 5 rule 5. A prediction made at or after its own deadline
    # cannot be distinguished from one made knowing the result, so it is not
    # evidence of anything — but only predictions/ makes that claim, and
    # backfilling a past gameweek into scratch/ is ordinary exploratory work.
    if now >= deadline_at:
        late = (
            f"generated_at_utc {generated_at} is at or after the GW{target_gw} "
            f"deadline {deadline}."
        )
        if _is_the_log(out_dir):
            raise SystemExit(
                late + "\nRefusing to write to the prediction log: an entry "
                "made after its deadline is indistinguishable from one made "
                "knowing the result.\nNothing was written."
            )
        print(f"WARNING: {late}\n"
              f"         Fine for an exploratory run in {out_dir}, but this "
              "could never go in predictions/.")

    # Provenance is write_entry's to state, from its own arguments. A row that
    # arrives carrying it is either a model that misunderstands the contract or
    # one forging where a prediction came from, and quietly overwriting the
    # value would leave both undiagnosed.
    forged = sorted({f for row in rows for f in PROVENANCE if f in row})
    if forged:
        raise SystemExit(
            f"Rows arrived carrying provenance columns: {forged}.\n"
            "write_entry stamps model_version, snapshot_id, generated_at_utc "
            "and deadline_utc from its own arguments — a model must not set "
            "them.\nNothing was written."
        )

    # Build the stamped rows up front rather than mutating the caller's dicts
    # during the write. validate_rows checks the entry exactly as it will be
    # written, and a caller that reuses its rows afterwards — writing the same
    # predictions to scratch/ and predictions/, say — does not find provenance
    # from the first write already sitting in them.
    stamped = [
        dict(row, model_version=model_version, snapshot_id=snapshot_id,
             generated_at_utc=generated_at, deadline_utc=deadline)
        for row in rows
    ]

    try:
        validate_rows(stamped, target_gw)
    except ValueError as e:
        raise SystemExit(
            f"{e}\nRefusing to write a malformed entry. Nothing was written.\n"
            "This is caught here, before the deadline, because after it the "
            "file would be immutable under hard rule 1."
        )

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
            for row in stamped:
                writer.writerow(row)
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
