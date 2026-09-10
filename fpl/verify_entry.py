"""Check a prediction log entry against the snapshot that produced it.

    python -m fpl.verify_entry predictions/gw04_baseline-v1.csv

The original plan for this was a human reading the top of the table before
committing: are the blanks and doubles right, are flagged players zeroed, is
anything absurd near the top. Every one of those questions is mechanical, and
all of them have to be answered correctly at around 11:45 on a Saturday
morning against a 654-row CSV. That is the wrong time to be reading a
spreadsheet, so this program answers them instead and exits non-zero if any
answer is wrong.

It is `log.validate_rows` plus the cross-checks that need the snapshot open
alongside the entry — row coverage, fixture counts, availability, and the
deadline. Those cannot live in `fpl/log.py`, which validates rows in memory at
write time and has no snapshot to compare them against.

The snapshot is chosen by the entry's own `snapshot_id` column, not by
data/raw/LATEST. An entry is evidence about the data it was built from, and by
the time anyone audits the track record LATEST has moved on many times.

Every check is a refusal to believe the entry, not a repair of it: nothing here
writes. Under hard rule 1 a committed entry cannot be corrected, so the only
useful moment for this program is before the commit.
"""

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from fpl.features import availability
from fpl.log import (
    FIELDS,
    INTEGER,
    MAX_REPORTED,
    NUMERIC,
    PROVENANCE,
    TIMESTAMP,
    parse_entry_filename,
    validate_rows,
)
from fpl.snapshot import fixture_counts, load_snapshot

# The Premier League is twenty clubs. An entry representing fewer has lost a
# whole team somewhere between the snapshot and the CSV, which no per-row check
# would notice — every surviving row can be perfectly well formed.
LEAGUE_SIZE = 20

FULL_MATCH_MINUTES = 90.0

# expected_minutes is written to 1dp, so a bound computed at full precision can
# sit half a unit in the last place below the value in the file without the
# entry being wrong.
ROUNDING = 0.05


def _timestamp(value, what, faults):
    """Parse a SPEC section 5 timestamp, recording a fault instead of raising."""
    try:
        return datetime.strptime(value, TIMESTAMP).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        faults.append(
            f"{what} is {value!r}, which is not a SPEC section 5 timestamp "
            "(YYYY-MM-DDTHH:MM:SSZ)"
        )
        return None


def read_entry(path):
    """Read a committed entry back into typed rows, as (rows, faults).

    Parsing is itself a check. Every column SPEC section 5 types as a number
    has to survive being read back as one, and an int column holding "1.0" is a
    fault rather than a value to coerce quietly — the scoring harness joins on
    player_id and does arithmetic on predicted_points.

    Faults here are structural, so the caller stops rather than continuing to
    the cross-checks: a row that could not be parsed would then be reported a
    second time as a player missing from the entry.
    """
    with Path(path).open(newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return [], [f"{path} is empty — not even a header row"]
        lines = list(reader)

    if header != FIELDS:
        return [], [
            "the column set does not match the SPEC section 5 contract, in "
            "which order is part of the contract:\n"
            f"      file: {header}\n"
            f"  expected: {FIELDS}"
        ]

    rows, faults = [], []
    for index, values in enumerate(lines):
        if len(values) != len(FIELDS):
            faults.append(
                f"row {index}: has {len(values)} fields, expected {len(FIELDS)}"
            )
            continue
        row = dict(zip(FIELDS, values))
        parsed = True
        for field in NUMERIC:
            text = row[field]
            try:
                row[field] = int(text) if field in INTEGER else float(text)
            except (TypeError, ValueError):
                kind = "an int" if field in INTEGER else "a number"
                faults.append(f"row {index}: {field} is {text!r}, not {kind}")
                parsed = False
        if parsed:
            rows.append(row)

    if not rows and not faults:
        faults.append(f"{path} has a header but no rows")
    return rows, faults


def _coverage_faults(rows, bootstrap):
    """Every player in the snapshot appears exactly once, and no one else does.

    SPEC section 5: an entry covers all players in the snapshot, not only the
    ones the model found interesting. A log that dropped the players the model
    thought were irrelevant could not be scored for the weeks it was wrong
    about that.
    """
    faults = []
    elements = bootstrap["elements"]
    if len(rows) != len(elements):
        faults.append(
            f"entry has {len(rows)} rows but the snapshot has {len(elements)} "
            "players"
        )

    expected = {p["id"] for p in elements}
    present = {row["player_id"] for row in rows}
    for label, ids in (("missing from the entry", sorted(expected - present)),
                       ("not in the snapshot", sorted(present - expected))):
        if ids:
            shown = ", ".join(str(i) for i in ids[:10])
            more = f" (and {len(ids) - 10} more)" if len(ids) > 10 else ""
            faults.append(f"{len(ids)} player_id(s) {label}: {shown}{more}")
    return faults


def _team_faults(rows, bootstrap, ids_by_name):
    faults = []
    unknown = sorted({row["team"] for row in rows} - set(ids_by_name))
    if unknown:
        faults.append(
            f"team short name(s) not in the snapshot: {', '.join(unknown)}"
        )

    present = {row["team"] for row in rows} - set(unknown)
    if len(present) < LEAGUE_SIZE:
        absent = sorted(set(ids_by_name) - present)
        faults.append(
            f"only {len(present)} teams are represented, expected at least "
            f"{LEAGUE_SIZE}. Absent: {', '.join(absent) or 'none named'}"
        )
    return faults


def _fixture_faults(rows, counts, ids_by_name, target_gw):
    """n_fixtures agrees with fixtures.json — the blank and double check.

    Done by comparison against the fixture list rather than by eye. A blank
    read as a single fixture inflates a whole club's predictions and a double
    read as a single halves them, and both look entirely ordinary in the
    table.

    Reported per team rather than per row. A club whose count is wrong is
    wrong for every one of its players, and thirty identical faults would bury
    the rest of the report.
    """
    faults = []
    disagreeing = {(row["team"], row["n_fixtures"],
                    counts.get(ids_by_name[row["team"]], 0))
                   for row in rows if row["team"] in ids_by_name}
    for team, found, expected in sorted(d for d in disagreeing
                                        if d[1] != d[2]):
        kind = {0: " (a blank)", 2: " (a double)"}.get(expected, "")
        faults.append(
            f"team {team}: n_fixtures is {found} in the entry, but "
            f"fixtures.json has {expected} fixture(s) in GW{target_gw}{kind}"
        )

    # No fixture, no points. Independent of any model, and the failure the
    # blank check exists to catch: the count can be recorded correctly and the
    # prediction still not act on it.
    playing = [row for row in rows
               if row["n_fixtures"] == 0 and row["predicted_points"] != 0]
    if playing:
        shown = ", ".join(
            f"{r['web_name']} ({r['team']}) {r['predicted_points']}"
            for r in playing[:5]
        )
        more = f" (and {len(playing) - 5} more)" if len(playing) > 5 else ""
        faults.append(
            f"{len(playing)} player(s) blanking in GW{target_gw} carry a "
            f"non-zero prediction: {shown}{more}"
        )
    return faults


def _availability_faults(rows, bootstrap):
    """A flagged player's prediction is reduced in line with the snapshot.

    Two claims, both model-independent. A player the snapshot says cannot play
    must be zeroed outright. A player on a partial chance must not be
    predicted more expected minutes than that chance allows — expected minutes
    are minutes times the probability of playing them, so the API's own
    percentage is a ceiling.

    The ceiling is deliberately loose for a double gameweek, where a model may
    reasonably report expected_minutes per fixture or across both. Catching an
    availability adjustment that never ran does not need a tight bound.
    """
    faults = []
    players = {p["id"]: p for p in bootstrap["elements"]}
    for row in rows:
        player = players.get(row["player_id"])
        if player is None:
            continue  # already reported as a player the snapshot does not have
        expected = availability(player)
        if expected >= 1.0:
            continue

        flagged = (
            f"{row['web_name']} ({row['player_id']}) is status "
            f"{player.get('status')!r}, chance_of_playing_next_round "
            f"{player.get('chance_of_playing_next_round')!r}"
        )
        if expected == 0.0:
            if row["expected_minutes"] != 0 or row["predicted_points"] != 0:
                faults.append(
                    f"{flagged}, so cannot play — but the entry predicts "
                    f"{row['predicted_points']} points off "
                    f"{row['expected_minutes']} minutes"
                )
        else:
            ceiling = (FULL_MATCH_MINUTES * expected
                       * max(row["n_fixtures"], 1)) + ROUNDING
            if row["expected_minutes"] > ceiling:
                faults.append(
                    f"{flagged} — but the entry predicts "
                    f"{row['expected_minutes']} expected minutes, above the "
                    f"{ceiling - ROUNDING:.1f} that chance allows"
                )
    return faults


def _provenance_faults(rows, bootstrap, target_gw, model_version, entry_path):
    """The provenance columns say one consistent thing about the whole entry.

    write_entry stamps all four from its own arguments, so they are identical
    in every row of anything it produced. An entry where they are not was
    assembled some other way, and the verifier's premise — that there is one
    snapshot and one deadline to check against — does not hold.
    """
    faults = []
    for field in PROVENANCE:
        values = sorted({row[field] for row in rows})
        if len(values) > 1:
            faults.append(
                f"{field} is not the same in every row: {values[:5]}"
                + (f" and {len(values) - 5} more" if len(values) > 5 else "")
            )
    if faults:
        return faults

    if rows[0]["model_version"] != model_version:
        faults.append(
            f"model_version is {rows[0]['model_version']!r} in the rows but "
            f"{model_version!r} in the filename {Path(entry_path).name!r}"
        )

    deadline = rows[0]["deadline_utc"]
    event = next((e for e in bootstrap["events"] if e["id"] == target_gw), None)
    if event is None:
        faults.append(f"the snapshot has no GW{target_gw} to take a deadline from")
    elif deadline != event["deadline_time"]:
        faults.append(
            f"deadline_utc is {deadline!r}, but the snapshot's GW{target_gw} "
            f"deadline is {event['deadline_time']!r}"
        )

    # SPEC section 5 rule 5, checked again on the committed file. write_entry
    # refuses a late entry, but only the file on disk is the evidence, and it
    # is what a reader auditing the log months later actually has.
    generated_at = _timestamp(rows[0]["generated_at_utc"], "generated_at_utc",
                              faults)
    deadline_at = _timestamp(deadline, "deadline_utc", faults)
    if generated_at and deadline_at and generated_at >= deadline_at:
        faults.append(
            f"generated_at_utc {rows[0]['generated_at_utc']} is at or after "
            f"the deadline {deadline} — a prediction made after its deadline "
            "cannot be distinguished from one made knowing the result"
        )
    return faults


def check_entry(rows, bootstrap, fixtures, target_gw, model_version, entry_path):
    """Every cross-check, as a list of faults. Empty means the entry is sound."""
    faults = []
    try:
        validate_rows(rows, target_gw)
    except ValueError as e:
        faults.append(str(e))

    team_names = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    ids_by_name = {name: tid for tid, name in team_names.items()}
    if len(ids_by_name) != len(team_names):
        # Short names are unique in every real bootstrap. If they ever are not,
        # the reverse map silently collapses two clubs together and the fixture
        # check compares the wrong counts, so stop rather than report nonsense.
        return faults + ["the snapshot has two teams sharing a short_name"]

    faults += _coverage_faults(rows, bootstrap)
    faults += _team_faults(rows, bootstrap, ids_by_name)
    faults += _fixture_faults(rows, fixture_counts(fixtures, target_gw),
                              ids_by_name, target_gw)
    faults += _availability_faults(rows, bootstrap)
    faults += _provenance_faults(rows, bootstrap, target_gw, model_version,
                                 entry_path)
    return faults


def _report(entry_path, faults):
    shown = faults[:MAX_REPORTED]
    more = (f"\n... and {len(faults) - MAX_REPORTED} more"
            if len(faults) > MAX_REPORTED else "")
    print(f"REFUSED {entry_path}\n"
          f"{len(faults)} problem(s):\n  " + "\n  ".join(shown) + more,
          file=sys.stderr)
    return 1


def main(path):
    entry_path = Path(path)
    if not entry_path.is_file():
        raise SystemExit(f"No entry at {entry_path}.")

    try:
        target_gw, model_version = parse_entry_filename(entry_path)
    except ValueError as e:
        raise SystemExit(str(e))

    rows, faults = read_entry(entry_path)
    if faults:
        return _report(entry_path, faults)

    # Read before the snapshot is opened, because it decides which snapshot to
    # open. Checked again in _provenance_faults alongside the other three, so
    # that a normal run reports it in the same place as everything else.
    snapshot_ids = sorted({row["snapshot_id"] for row in rows})
    if len(snapshot_ids) != 1:
        return _report(entry_path, [
            f"snapshot_id is not the same in every row: {snapshot_ids[:5]}. "
            "There is no single snapshot to check this entry against."
        ])

    snap, bootstrap, fixtures, _ = load_snapshot(snapshot_ids[0])
    faults = check_entry(rows, bootstrap, fixtures, target_gw, model_version,
                         entry_path)
    if faults:
        return _report(entry_path, faults)

    counts = fixture_counts(fixtures, target_gw)
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    blanks = sorted(teams[t] for t in teams if counts.get(t, 0) == 0)
    doubles = sorted(teams[t] for t in teams if counts.get(t, 0) > 1)
    deadline = rows[0]["deadline_utc"]
    margin = (datetime.strptime(deadline, TIMESTAMP)
              - datetime.strptime(rows[0]["generated_at_utc"], TIMESTAMP))

    print(f"entry:     {entry_path}")
    print(f"snapshot:  {snap.name}")
    print(f"model:     {model_version}")
    print(f"target:    GW{target_gw}")
    print(f"deadline:  {deadline}")
    print(f"generated: {rows[0]['generated_at_utc']}  "
          f"({margin} before the deadline)")
    print(f"rows:      {len(rows)} players across "
          f"{len({r['team'] for r in rows})} teams")
    print(f"blanking:  {', '.join(blanks) or 'none'}")
    print(f"doubles:   {', '.join(doubles) or 'none'}")
    print(f"\nOK — entry agrees with snapshot {snap.name} on every check.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Verify a prediction log entry against its own snapshot."
    )
    ap.add_argument("entry", help="path to a predictions/gw<NN>_<model>.csv")
    sys.exit(main(ap.parse_args().entry))
