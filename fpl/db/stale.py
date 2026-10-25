"""Whether the database is behind the FPL API, from a bootstrap-only snapshot.

    fpl-fetch --skip-players          # seconds
    fpl-stale                         # the newest snapshot, bootstrap only or not
    fpl-stale --schema-only

The scheduled ingest (#93) runs this before the six-minute full fetch, so a
day when nothing has happened costs a few requests instead of one per player.
A full load is needed when the database disagrees with the API about any
gameweek (deadline, finished, data_checked), any fixture (gameweek, kickoff,
finished, score), any team, or which players there are, their names and
positions. Player stats change only when a fixture finishes or a gameweek's
bonus is confirmed, so the first two cover them.

It also refuses to go on while the schema is behind this code: the ingest
runs from `main`, but the production schema is migrated only by the deploy,
which may not have run yet. Exits 1 then; otherwise prints what differs and,
under GitHub Actions, writes `needed=true|false` to its step outputs.

Run from the repository root, with DATABASE_URL set.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import select

from fpl.db.load import POSITIONS, season_of, timestamp
from fpl.db.migrate import current, head
from fpl.db.models import Fixture, Gameweek, SeasonPlayer, Snapshot, Team
from fpl.db.session import exits_on_bad_url, make_engine
from fpl.paths import require_project_root

RAW = Path("data/raw")
SHOWN = 5


def from_api(bootstrap, fixtures):
    """What the load would write for the compared tables, keyed as the database keys it."""
    return {
        "gameweeks": {e["id"]: (timestamp(e["deadline_time"]), e["finished"], e["data_checked"])
                      for e in bootstrap["events"]},
        "fixtures": {f["id"]: (f["event"], timestamp(f["kickoff_time"]), f["finished"],
                               f["team_h_score"], f["team_a_score"])
                     for f in fixtures},
        "teams": {t["id"]: (t["code"], t["name"], t["short_name"]) for t in bootstrap["teams"]},
        "players": {e["id"]: (e["web_name"], POSITIONS[e["element_type"]])
                    for e in bootstrap["elements"] if e["element_type"] in POSITIONS},
    }


def from_database(connection, season):
    """The same tables as from_api, as the database has them for `season`."""
    def rows(*columns, where):
        return connection.execute(select(*columns).where(where)).all()

    return {
        "gameweeks": {r[0]: tuple(r[1:]) for r in rows(
            Gameweek.gameweek, Gameweek.deadline_utc, Gameweek.finished, Gameweek.data_checked,
            where=Gameweek.season == season)},
        "fixtures": {r[0]: tuple(r[1:]) for r in rows(
            Fixture.fixture_id, Fixture.gameweek, Fixture.kickoff_utc, Fixture.finished,
            Fixture.team_h_score, Fixture.team_a_score, where=Fixture.season == season)},
        "teams": {r[0]: tuple(r[1:]) for r in rows(
            Team.team_id, Team.code, Team.name, Team.short_name, where=Team.season == season)},
        "players": {r[0]: tuple(r[1:]) for r in rows(
            SeasonPlayer.element_id, SeasonPlayer.web_name, SeasonPlayer.position,
            where=SeasonPlayer.season == season)},
    }


def differences(api, database):
    """One line per table that differs, naming up to SHOWN of its keys."""
    found = []
    for table, wanted in api.items():
        have = database.get(table, {})
        keys = sorted(k for k in wanted.keys() | have.keys() if wanted.get(k) != have.get(k))
        if keys:
            shown = ", ".join(str(k) for k in keys[:SHOWN])
            more = f" and {len(keys) - SHOWN} more" if len(keys) > SHOWN else ""
            found.append(f"{table}: {len(keys)} differ ({shown}{more})")
    return found


def newest_snapshot():
    """The newest complete snapshot under data/raw/, bootstrap only or not.

    Not LATEST: a bootstrap-only fetch leaves that pointer alone, and the
    bootstrap-only one is the one to compare.
    """
    complete = sorted(p for p in RAW.iterdir() if (p / "manifest.json").exists()) \
        if RAW.is_dir() else []
    if not complete:
        raise SystemExit("No complete snapshot under data/raw/. Run: fpl-fetch --skip-players")
    return complete[-1]


def write_output(needed, environ=None):
    environ = os.environ if environ is None else environ
    path = environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as out:
            out.write(f"needed={'true' if needed else 'false'}\n")


@exits_on_bad_url
def main(schema_only=False):
    engine = make_engine()
    try:
        with engine.connect() as connection:
            at, latest = current(connection), head()
            if at != latest:
                raise SystemExit(
                    f"The database schema is at {at or 'nothing'}, and this code needs {latest}. "
                    "The deploy migrates it; loading now could fail part-way, so this waits "
                    "for the deploy rather than racing it.")
            print(f"The schema is at {latest}, as this code expects.")
            if schema_only:
                return 0
            snap = newest_snapshot()
            bootstrap = json.loads((snap / "bootstrap.json").read_text())
            fixtures = json.loads((snap / "fixtures.json").read_text())
            season = season_of(bootstrap["events"])
            loaded = connection.scalar(select(Snapshot.snapshot_id).where(
                Snapshot.season == season).order_by(Snapshot.snapshot_id.desc()).limit(1))
            if loaded is None:
                found = [f"nothing is loaded for {season}"]
            else:
                found = differences(from_api(bootstrap, fixtures),
                                    from_database(connection, season))
    finally:
        engine.dispose()

    print(f"Compared snapshot {snap.name} with the database ({season}).")
    if found:
        print("A full load is needed:")
        for line in found:
            print(f"  {line}")
    else:
        print(f"Nothing has changed since snapshot {loaded}: no load needed.")
    write_output(bool(found))
    return 0


def cli(argv=None):
    """`fpl-stale`, or `python -m fpl.db.stale`."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--schema-only", action="store_true",
                    help="only check that the schema is at this code's head")
    args = ap.parse_args(argv)
    require_project_root()
    return main(args.schema_only)


if __name__ == "__main__":
    sys.exit(cli())
