"""Load a snapshot into Postgres.

    fpl-load                              # data/raw/LATEST
    fpl-load --snapshot 20261020T185915Z

Writes teams, players, gameweeks, fixtures, club spells and per-fixture player
stats, all in one transaction, so a snapshot is loaded completely or not at
all. Loading a snapshot that is already loaded changes nothing.

Snapshots are applied newest-wins. One newer than any already loaded for its
season updates existing rows. An older one only fills rows that are missing, so
loading out of order never rolls the database back.

Player stats come from each player's history, one row per fixture, and only for
finished fixtures: a mid-gameweek snapshot carries zeroed rows for matches not
yet played. Bonus on a finished fixture can still move until `data_checked`;
the next snapshot loaded overwrites it.

Run from the repository root, with DATABASE_URL set.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Numeric, delete, func, select
from sqlalchemy.dialects.postgresql import Range, insert

from fpl.db.models import (
    Fixture,
    Gameweek,
    Player,
    PlayerClub,
    PlayerFixtureStats,
    Season,
    SeasonPlayer,
    Snapshot,
    Team,
)
from fpl.db.session import exits_on_bad_url, make_engine
from fpl.paths import require_project_root
from fpl.snapshot import load_snapshot

POSITIONS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
LAST_GAMEWEEK = 38
# Any fixed key will do: it only has to be the same for every fpl-load.
LOAD_LOCK = 78


@dataclass
class Report:
    snapshot_id: str
    season: str
    already_loaded: bool = False
    newest: bool = True
    rows: dict[str, int] = field(default_factory=dict)
    skipped_elements: list[int] = field(default_factory=list)

    def __str__(self):
        if self.already_loaded:
            return f"Snapshot {self.snapshot_id} is already loaded. Nothing changed."
        mode = ("newest for the season: rows updated" if self.newest
                else "older than one already loaded: only missing rows added")
        lines = [f"Loaded snapshot {self.snapshot_id} ({self.season}), {mode}.",
                 "Rows in the snapshot, per table:"]
        lines += [f"  {table:<22}{count:>7}" for table, count in self.rows.items()]
        if self.skipped_elements:
            lines.append(f"Skipped {len(self.skipped_elements)} elements that are not "
                         f"players (element_type outside 1-4): {self.skipped_elements}")
        return "\n".join(lines)


def season_of(events):
    """'2026/27' for a season whose first deadline falls in August 2026."""
    start = min(timestamp(e["deadline_time"]) for e in events).year
    return f"{start}/{(start + 1) % 100:02d}"


def timestamp(value):
    return None if value is None else datetime.fromisoformat(value)


def upsert(connection, model, rows, newest):
    """Insert rows; on a key that exists, update it only from the newest snapshot."""
    if not rows:
        return 0
    table = model.__table__
    keys = [c.name for c in table.primary_key]
    statement = insert(model)
    updates = {c.name: statement.excluded[c.name] for c in table.columns
               if c.name not in keys and c.name in rows[0]}
    if newest and updates:
        statement = statement.on_conflict_do_update(index_elements=keys, set_=updates)
    else:
        statement = statement.on_conflict_do_nothing(index_elements=keys)
    connection.execute(statement, rows)
    return len(rows)


def club_spells(history, fixtures_by_id, current_team, next_gameweek):
    """A player's clubs this season, as (team_id, gameweeks) ranges.

    The club for each fixture played comes from the fixture itself: the home
    side if the player was at home. The bootstrap's current club then runs from
    the next gameweek, if it differs from the last one in the history. If the
    club changed inside a gameweek, the later club has that gameweek.
    """
    starts = []
    for row in sorted(history, key=lambda h: (h["round"], h.get("kickoff_time") or "")):
        fixture = fixtures_by_id.get(row["fixture"])
        if fixture is None:
            continue
        team = fixture["team_h"] if row["was_home"] else fixture["team_a"]
        if starts and starts[-1][0] == team:
            continue
        if starts and starts[-1][1] == row["round"]:
            starts.pop()
            # The club before this gameweek may be the one it ends at.
            if not (starts and starts[-1][0] == team):
                starts.append((team, row["round"]))
        else:
            starts.append((team, row["round"]))

    if not starts or starts[-1][0] != current_team:
        start = next_gameweek or LAST_GAMEWEEK + 1
        if starts:
            start = max(start, starts[-1][1] + 1)
        elif next_gameweek is None:
            start = 1
        if start <= LAST_GAMEWEEK:
            starts.append((current_team, start))

    ends = [s for _, s in starts[1:]] + [LAST_GAMEWEEK + 1]
    return [(team, Range(start, end)) for (team, start), end in zip(starts, ends, strict=True)]


def stats_row(season, snapshot_id, history_row):
    """A history row as a player_fixture_stats row: same column names as the API."""
    row = {"season": season, "element_id": history_row["element"],
           "fixture_id": history_row["fixture"], "snapshot_id": snapshot_id}
    for column in PlayerFixtureStats.__table__.columns:
        if column.name in row:
            continue
        value = history_row.get(column.name)
        # The API sends expected goals and ICT values as strings: "0.12".
        if value is not None and isinstance(column.type, Numeric):
            value = Decimal(value)
        row[column.name] = value
    return row


def load(engine, snap, bootstrap, fixtures, players_dir):
    manifest = json.loads((snap / "manifest.json").read_text())
    snapshot_id = manifest["snapshot_id"]
    season = season_of(bootstrap["events"])
    report = Report(snapshot_id, season)

    with engine.begin() as connection:
        # One load at a time: which snapshot is newest is read below and acted
        # on before commit, so two loads at once could both think they were.
        connection.execute(select(func.pg_advisory_xact_lock(LOAD_LOCK)))
        if connection.scalar(select(Snapshot.snapshot_id).where(
                Snapshot.snapshot_id == snapshot_id)):
            report.already_loaded = True
            return report

        latest = connection.scalar(
            select(func.max(Snapshot.snapshot_id)).where(Snapshot.season == season))
        report.newest = latest is None or snapshot_id > latest
        # Players are not per season, so only a snapshot newer than every one
        # loaded, of any season, may rename them.
        latest_any = connection.scalar(select(func.max(Snapshot.snapshot_id)))
        newest_overall = latest_any is None or snapshot_id > latest_any

        connection.execute(insert(Season).values(season=season).on_conflict_do_nothing())
        connection.execute(insert(Snapshot).values(
            snapshot_id=snapshot_id, season=season,
            completed_at_utc=timestamp(manifest["completed_at_utc"]),
            element_count=manifest["element_count"], has_players=manifest["has_players"],
            live_gameweeks=manifest.get("live_gameweeks", [])))

        rows = report.rows
        rows["teams"] = upsert(connection, Team, [
            {"season": season, "team_id": t["id"], "code": t["code"], "name": t["name"],
             "short_name": t["short_name"]} for t in bootstrap["teams"]], report.newest)

        elements = []
        for e in bootstrap["elements"]:
            if e["element_type"] in POSITIONS:
                elements.append(e)
            else:
                report.skipped_elements.append(e["id"])
        rows["players"] = upsert(connection, Player, [
            {"code": e["code"], "first_name": e["first_name"], "second_name": e["second_name"]}
            for e in elements], newest_overall)
        rows["season_players"] = upsert(connection, SeasonPlayer, [
            {"season": season, "element_id": e["id"], "player_code": e["code"],
             "web_name": e["web_name"], "position": POSITIONS[e["element_type"]]}
            for e in elements], report.newest)

        rows["gameweeks"] = upsert(connection, Gameweek, [
            {"season": season, "gameweek": e["id"], "deadline_utc": timestamp(e["deadline_time"]),
             "finished": e["finished"], "data_checked": e["data_checked"]}
            for e in bootstrap["events"]], report.newest)
        rows["fixtures"] = upsert(connection, Fixture, [
            {"season": season, "fixture_id": f["id"], "gameweek": f["event"],
             "kickoff_utc": timestamp(f["kickoff_time"]), "team_h": f["team_h"],
             "team_a": f["team_a"], "team_h_score": f["team_h_score"],
             "team_a_score": f["team_a_score"], "finished": f["finished"]}
            for f in fixtures], report.newest)

        fixtures_by_id = {f["id"]: f for f in fixtures}
        unfinished = [e["id"] for e in bootstrap["events"] if not e["finished"]]
        next_gameweek = min(unfinished) if unfinished else None
        spells, stats = [], []
        for e in elements:
            history = json.loads((players_dir / f"{e['id']}.json").read_text())["history"]
            for team, gameweeks in club_spells(history, fixtures_by_id, e["team"],
                                               next_gameweek):
                spells.append({"season": season, "element_id": e["id"], "team_id": team,
                               "gameweeks": gameweeks})
            stats += [stats_row(season, snapshot_id, h) for h in history
                      if fixtures_by_id.get(h["fixture"], {}).get("finished")]

        # Spells are derived whole from one snapshot's histories, so the newest
        # snapshot replaces them rather than merging with an older derivation.
        # An older one only adds spells for players who have none.
        if report.newest:
            connection.execute(delete(PlayerClub).where(PlayerClub.season == season))
        else:
            have_spells = set(connection.scalars(
                select(PlayerClub.element_id).where(PlayerClub.season == season)))
            spells = [s for s in spells if s["element_id"] not in have_spells]
        rows["player_clubs"] = upsert(connection, PlayerClub, spells, report.newest)
        rows["player_fixture_stats"] = upsert(connection, PlayerFixtureStats, stats,
                                              report.newest)
    return report


@exits_on_bad_url
def main(snapshot_id=None):
    # The URL first: a bad one is refused before the snapshot's files are read.
    engine = make_engine()
    try:
        snap, bootstrap, fixtures, players_dir = load_snapshot(snapshot_id)
        print(load(engine, snap, bootstrap, fixtures, players_dir))
    finally:
        engine.dispose()
    return 0


def cli(argv=None):
    """`fpl-load`, or `python -m fpl.db.load`."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--snapshot", default=None, metavar="SNAPSHOT_ID",
                    help="the snapshot to load (default: LATEST)")
    args = ap.parse_args(argv)
    require_project_root()
    return main(args.snapshot)


if __name__ == "__main__":
    sys.exit(cli())
