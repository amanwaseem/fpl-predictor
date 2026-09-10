"""Reading a raw snapshot off disk, and bounding what may be read from it.

Nothing here is model-specific. Every consumer of a snapshot needs the same
integrity checks and the same rule 2 boundaries — the baseline predictor, the
scoring harness, and each component model — so they live here rather than
inside whichever model happened to need them first.
"""

import json
import re
from pathlib import Path

# fetch.py names snapshots by UTC timestamp: datetime.strftime("%Y%m%dT%H%M%SZ").
# Matched rather than assumed, because a snapshot id reaching load_snapshot may
# have come out of a CSV column in a committed entry, and "" or "/etc" would
# otherwise be joined onto data/raw and resolve somewhere unintended.
SNAPSHOT_ID = re.compile(r"^\d{8}T\d{6}Z$")


def load_snapshot(snapshot_id=None):
    """Load a raw snapshot written by fpl/fetch.py, most recent by default.

    `snapshot_id` names a particular snapshot directory instead. Verifying a
    committed entry needs that: an entry has to be checked against the
    snapshot that produced it, which the entry records in its own snapshot_id
    column and which is not the latest one for long — the next fetch moves
    LATEST, and a log entry stays readable for the rest of the season.
    """
    if snapshot_id is None:
        latest_file = Path("data/raw/LATEST")
        if not latest_file.exists():
            raise SystemExit("No snapshot found. Run: python -m fpl.fetch")
        source = "data/raw/LATEST"
        snapshot_id = latest_file.read_text().strip()
    else:
        source = "the requested snapshot id"

    # Checked before the join, not after. An id that is empty, absolute, or
    # contains a path separator escapes data/raw entirely — "" resolves to the
    # directory itself and "/etc" ignores the join — and the resulting
    # directory would be reported below as an interrupted fetch to resume,
    # which is both wrong and destructive advice.
    if not SNAPSHOT_ID.match(snapshot_id):
        raise SystemExit(
            f"{snapshot_id!r} from {source} is not a snapshot id.\n"
            "Expected a UTC timestamp as written by fpl.fetch, "
            "'20260907T141922Z' in the form YYYYMMDDTHHMMSSZ."
        )

    snap = Path("data/raw") / snapshot_id
    players_dir = snap / "players"

    # Checked before the manifest, so that a snapshot that was never fetched
    # is not reported as an interrupted fetch to resume. data/raw is
    # gitignored, so an entry committed months ago routinely names a snapshot
    # that is simply not on this machine.
    if not snap.is_dir():
        raise SystemExit(
            f"No snapshot at {snap}.\n"
            "data/raw/ is gitignored and snapshots are not shared, so this one "
            "may never have existed here.\n"
            "Take a fresh one with: python -m fpl.fetch"
        )

    manifest_path = snap / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(
            f"Snapshot {snap} has no manifest.json — it is incomplete.\n"
            "An interrupted fetch leaves a directory that looks finished but is "
            "missing players, and those players would be dropped from the "
            "prediction log silently.\n"
            "Resume it with: python -m fpl.fetch --resume"
        )

    manifest = json.loads(manifest_path.read_text())
    if not manifest.get("has_players"):
        raise SystemExit(
            f"Snapshot {snap} was taken with --skip-players and has no "
            "per-player history.\nRun: python -m fpl.fetch"
        )
    if not players_dir.is_dir():
        raise SystemExit(
            f"Snapshot {snap} claims player history but has no players/ "
            "directory.\nRun: python -m fpl.fetch"
        )

    bootstrap = json.loads((snap / "bootstrap.json").read_text())
    fixtures = json.loads((snap / "fixtures.json").read_text())

    # Belt and braces: the manifest says the fetch finished, but verify the
    # files are actually on disk before building a log entry from them. A
    # prediction missing an arbitrary subset of players is worse than no
    # prediction, because nothing in the committed CSV reveals the gap.
    missing = [p["id"] for p in bootstrap["elements"]
               if not (players_dir / f"{p['id']}.json").exists()]
    if missing:
        shown = ", ".join(str(i) for i in missing[:10])
        more = f" (and {len(missing) - 10} more)" if len(missing) > 10 else ""
        raise SystemExit(
            f"Snapshot {snap} claims {manifest.get('element_count')} players but "
            f"{len(missing)} history files are missing: {shown}{more}.\n"
            "Refusing to write a partial prediction log.\n"
            "Resume it with: python -m fpl.fetch --resume"
        )

    return snap, bootstrap, fixtures, players_dir


def resolve_target_gw(events, requested):
    """Pick the gameweek to predict, and return it with its deadline.

    Refuses to target a gameweek earlier than the snapshot's own next one.
    usable_rounds bounds *history* to rounds before the target, but the
    bootstrap fields are not bounded: chance_of_playing_next_round means "next
    round as of this snapshot", and now_cost and status are snapshot-time too.
    Predicting forward is fine. Backtesting GW4 from a later snapshot would
    feed post-deadline availability and price into the model — a rule 2
    violation that leaves no trace in the output.
    """
    upcoming = next((e for e in events if e.get("is_next")), None)
    if upcoming is None:
        # Between the last deadline and the API flipping the flag, and at
        # season end, no event carries is_next. Fall back to the first
        # unfinished gameweek; if every gameweek is finished, any target is a
        # backtest and there is no safe forward boundary at all.
        upcoming = next((e for e in events if not e.get("finished")), None)

    if requested is not None:
        event = next((e for e in events if e["id"] == requested), None)
        if event is None:
            raise SystemExit(f"No gameweek {requested} in this snapshot.")
        if upcoming is None:
            raise SystemExit(
                f"Refusing to predict GW{requested}: this snapshot has no "
                "upcoming gameweek, so every target is in the past.\n"
                "Availability, price and status would all be post-deadline."
            )
        if requested < upcoming["id"]:
            raise SystemExit(
                f"Refusing to predict GW{requested} from a snapshot whose next "
                f"gameweek is GW{upcoming['id']}.\n"
                "Availability, price and status in this snapshot are all "
                "post-deadline for GW{}, so the prediction would be "
                "contaminated.\n"
                "Backtesting needs a snapshot taken before that deadline."
                .format(requested)
            )
    else:
        event = next((e for e in events if e.get("is_next")), None)
        if event is None:
            raise SystemExit("No upcoming gameweek found — season may be over.")
    return event["id"], event["deadline_time"]


def fixture_counts(fixtures, target_gw):
    """How many fixtures each team has in the target gameweek.

    Almost always 1, but blanks (0) and doubles (2) happen and are the most
    common source of badly wrong predictions.
    """
    counts = {}
    for f in fixtures:
        if f.get("event") != target_gw:
            continue
        for side in ("team_h", "team_a"):
            counts[f[side]] = counts.get(f[side], 0) + 1
    return counts


def usable_rounds(events, target_gw):
    """Split rounds before the target into (usable, excluded).

    A round is usable only once its results are final. The API reports that as
    data_checked, which flips true when bonus points are settled.

    A snapshot taken mid-gameweek still carries a history row for every player,
    including those whose fixture has not kicked off. That row reads 0 minutes
    and 0 points and is indistinguishable from a genuine non-appearance, so
    including it silently scores an unplayed match as a benching.
    """
    before = [e for e in events if e["id"] < target_gw]
    usable = {e["id"] for e in before if e.get("data_checked")}
    excluded = sorted(e["id"] for e in before if not e.get("data_checked"))
    return usable, excluded
