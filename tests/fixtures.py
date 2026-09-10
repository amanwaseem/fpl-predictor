"""Synthetic snapshots and entries, shared by every test suite.

Four test modules hand-rolling snapshot JSON would drift apart, and a fixture
that has drifted from the real schema tests nothing — a verifier proved
correct against an invented shape says nothing about the file it will be run
on at 11:45 on a Saturday. The builders live here so that when the FPL schema
moves, one place has to change.

Nothing here calls the network. Every builder writes into a caller-supplied
temporary directory.
"""

import csv
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fpl.features import availability
from fpl.log import FIELDS, TIMESTAMP
from fpl.snapshot import fixture_counts

# Distinguishes "derive a sensible default" from an explicit None, which for
# `manifest` means "write no manifest.json at all" — the interrupted-fetch
# case test_snapshot_integrity needs.
DERIVE = object()

POSITIONS = [(1, "GKP"), (2, "DEF"), (3, "MID"), (4, "FWD")]
ELEMENT_TYPES = [{"id": i, "singular_name_short": name} for i, name in POSITIONS]

# The Premier League is 20 clubs, and the verifier refuses an entry
# representing fewer. Short names are three characters like the real ones.
LEAGUE_SIZE = 20


def future_deadline(hours=24):
    """A deadline far enough ahead that log.write_entry's rule 5 guard passes.

    Computed rather than hard-coded. A fixed date would quietly turn the
    end-to-end test red once it passed, and the failure would read as a bug in
    the predictor rather than as a stale fixture.
    """
    at = datetime.now(timezone.utc) + timedelta(hours=hours)
    return at.strftime(TIMESTAMP)


def past_timestamp(hours=1):
    """A timestamp that far in the past, for entries that must look late."""
    at = datetime.now(timezone.utc) - timedelta(hours=hours)
    return at.strftime(TIMESTAMP)


def team(tid, short_name=None):
    return {"id": tid, "short_name": short_name or f"T{tid:02d}"}


def element(pid, *, team=1, element_type=3, status="a", chance=None,
            web_name=None, now_cost=50):
    return {
        "id": pid,
        "web_name": web_name or f"p{pid}",
        "element_type": element_type,
        "team": team,
        "now_cost": now_cost,
        "status": status,
        "chance_of_playing_next_round": chance,
    }


def event(gw, *, deadline, data_checked=False, finished=False, is_next=False):
    return {"id": gw, "deadline_time": deadline, "data_checked": data_checked,
            "finished": finished, "is_next": is_next}


def season(target_gw, deadline, *, checked_through=None):
    """Events for gameweeks 1..target_gw, with the target flagged is_next.

    Rounds before the target are finished and data_checked, so usable_rounds
    admits them. `checked_through` leaves the rounds after it provisional,
    which is how a mid-gameweek snapshot looks.
    """
    if checked_through is None:
        checked_through = target_gw - 1
    past = [event(gw, deadline=f"2026-08-{13 + gw:02d}T17:30:00Z",
                  data_checked=gw <= checked_through, finished=True)
            for gw in range(1, target_gw)]
    return past + [event(target_gw, deadline=deadline, is_next=True)]


def fixture(gw, home, away, fid=None):
    return {"id": fid if fid is not None else home * 100 + away,
            "event": gw, "team_h": home, "team_a": away}


def full_round(gw, team_ids):
    """One fixture per pair of teams — every club plays exactly once."""
    ids = list(team_ids)
    return [fixture(gw, ids[i], ids[i + 1]) for i in range(0, len(ids) - 1, 2)]


def history(rounds, *, minutes=90, points=5):
    """A player's per-round history rows, in the element-summary shape."""
    return [{"round": gw, "minutes": minutes, "total_points": points}
            for gw in rounds]


def snapshot(root, name, *, elements=None, teams=None, element_types=None,
             events=None, fixtures=None, histories=None, player_ids=None,
             manifest=DERIVE, latest=False):
    """Write a snapshot under `root`/data/raw/`name` and return its path.

    `player_ids` controls which per-player history files are written, which is
    how a snapshot missing some of them is built; it defaults to every
    element, the complete case. `histories` maps a player id to its history
    rows, defaulting to an empty history.
    """
    directory = Path(root) / "data/raw" / name
    (directory / "players").mkdir(parents=True)

    elements = [element(1)] if elements is None else elements
    teams = [team(1)] if teams is None else teams
    element_types = ELEMENT_TYPES if element_types is None else element_types
    events = season(2, "2026-08-28T17:30:00Z") if events is None else events
    fixtures = [] if fixtures is None else fixtures
    histories = {} if histories is None else histories
    if player_ids is None:
        player_ids = [e["id"] for e in elements]

    for pid in player_ids:
        (directory / "players" / f"{pid}.json").write_text(
            json.dumps({"history": histories.get(pid, [])})
        )

    (directory / "bootstrap.json").write_text(json.dumps({
        "elements": elements,
        "teams": teams,
        "element_types": element_types,
        "events": events,
    }))
    (directory / "fixtures.json").write_text(json.dumps(fixtures))

    if manifest is DERIVE:
        manifest = {"snapshot_id": name, "element_count": len(elements),
                    "has_players": True}
    if manifest is not None:
        (directory / "manifest.json").write_text(json.dumps(manifest))

    if latest:
        (Path(root) / "data/raw/LATEST").write_text(name)
    return directory


def league_snapshot(root, name="20260910T000000Z", *, target_gw=4,
                    deadline=None, per_team=4, latest=True, events=None,
                    **kwargs):
    """A full 20-team snapshot with a played history and one round of fixtures.

    The shape the verifier and the end-to-end test both need: enough teams to
    clear the league-size check, one fixture per club in the target gameweek,
    and settled history in every round before it.
    """
    deadline = future_deadline() if deadline is None else deadline
    teams = [team(t) for t in range(1, LEAGUE_SIZE + 1)]

    elements, histories = [], {}
    pid = 0
    for t in range(1, LEAGUE_SIZE + 1):
        for slot in range(per_team):
            pid += 1
            position = POSITIONS[slot % len(POSITIONS)][0]
            elements.append(element(pid, team=t, element_type=position,
                                    now_cost=45 + slot * 5))
            histories[pid] = history(range(1, target_gw),
                                     minutes=90 - slot * 5, points=4 + slot)

    return snapshot(
        root, name,
        elements=elements,
        teams=teams,
        events=season(target_gw, deadline) if events is None else events,
        fixtures=full_round(target_gw, range(1, LEAGUE_SIZE + 1)),
        histories=histories,
        latest=latest,
        **kwargs
    )


def entry_rows(bootstrap, fixtures, target_gw, deadline, *,
               snapshot_id="20260910T000000Z", model_version="baseline-v1",
               generated_at=None):
    """A clean prediction log entry for a snapshot, in contract sort order.

    Built directly rather than by running a model, so that the verifier's
    tests fail for the reason they name and not because the baseline changed
    its mind. The numbers are arbitrary; their *consistency* with the snapshot
    is the point — a flagged player is zeroed, a blanking club gets zero
    fixtures, and the rows come out sorted.
    """
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    positions = {p["id"]: p["singular_name_short"]
                 for p in bootstrap["element_types"]}
    counts = fixture_counts(fixtures, target_gw)
    generated_at = past_timestamp() if generated_at is None else generated_at

    rows = []
    for player in bootstrap["elements"]:
        n_fixtures = counts.get(player["team"], 0)
        avail = availability(player)
        if n_fixtures == 0 or avail == 0:
            predicted, minutes, pp90 = 0.0, 0.0, 0.0
        else:
            minutes = round(90.0 * avail, 1)
            pp90 = round(4.0 + (player["id"] % 7) * 0.1, 2)
            predicted = round(pp90 * (minutes / 90.0) * n_fixtures, 2)

        rows.append({
            "gameweek": target_gw,
            "player_id": player["id"],
            "web_name": player["web_name"],
            "team": teams[player["team"]],
            "position": positions[player["element_type"]],
            "price": player["now_cost"] / 10.0,
            "predicted_points": predicted,
            "expected_minutes": minutes,
            "points_per_90": pp90,
            "n_fixtures": n_fixtures,
            "status": player.get("status", "a"),
            "model_version": model_version,
            "snapshot_id": snapshot_id,
            "generated_at_utc": generated_at,
            "deadline_utc": deadline,
        })

    rows.sort(key=lambda r: (-r["predicted_points"], r["player_id"]))
    return rows


def write_csv(path, rows, fieldnames=FIELDS):
    """Write rows to a CSV, bypassing log.write_entry's guards.

    Deliberately not write_entry: these tests need to produce entries that
    write_entry would refuse, in order to prove the verifier catches them when
    they arrive from somewhere else — a hand-edited file, or a model written
    before the schema was frozen.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def read_csv(path):
    """Read an entry back as raw strings, for tests that mutate one."""
    with Path(path).open(newline="") as f:
        return list(csv.DictReader(f))


class TempCwd(unittest.TestCase):
    """Each test gets its own data/raw, so nothing touches real snapshots.

    The predictor and the verifier both resolve paths against the working
    directory, so the working directory is what the tests have to control.
    """

    def setUp(self):
        self.prev = Path.cwd()
        self.tmp = Path(tempfile.mkdtemp())
        os.chdir(self.tmp)
        (self.tmp / "data/raw").mkdir(parents=True)

    def tearDown(self):
        os.chdir(self.prev)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def snapshot(self, name, **kwargs):
        return snapshot(self.tmp, name, **kwargs)

    def league(self, name="20260910T000000Z", **kwargs):
        return league_snapshot(self.tmp, name, **kwargs)
