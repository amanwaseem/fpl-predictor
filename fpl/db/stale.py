"""Whether the database is behind the FPL API, from a bootstrap-only snapshot.

    fpl-fetch --skip-players          # seconds
    fpl-stale                         # the newest snapshot, bootstrap only or not
    fpl-stale --schema-only

The scheduled ingest (#93) runs this before the six-minute full fetch, so a
day when nothing has happened costs a few requests instead of one per player.
A full load is needed when the database disagrees with the API about any row
the load writes straight from the bootstrap and fixtures (gameweeks, fixtures,
teams, players), built by the load's own row builders so the two cannot
drift, or about any player's current club. Player stats change only when a
fixture finishes or a gameweek's bonus is confirmed, so gameweeks and fixtures
cover them.

It also refuses to go on unless the schema is at this code's head: the
ingest runs from `main`, but the production schema is migrated only by the
deploy, which may be behind it or, if main moved on mid-run, ahead. Exits 1
then; otherwise prints what differs and, under GitHub Actions, writes
`needed=true|false` to its step outputs.

Run from the repository root, with DATABASE_URL set.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from fpl.db.load import (
    fixture_rows,
    gameweek_rows,
    player_elements,
    season_of,
    season_player_rows,
    team_rows,
)
from fpl.db.migrate import MIGRATIONS, current, head
from fpl.db.models import (
    Fixture,
    Gameweek,
    IngestCheck,
    PlayerClub,
    SeasonPlayer,
    Snapshot,
    Team,
)
from fpl.db.session import exits_on_bad_url, make_engine
from fpl.paths import require_project_root

RAW = Path("data/raw")
SHOWN = 5
TABLES = {"gameweeks": Gameweek, "fixtures": Fixture, "teams": Team, "players": SeasonPlayer}


def _split(model):
    """A table's key columns and the rest, without the season, in the table's order."""
    columns = [c for c in model.__table__.columns if c.name != "season"]
    return [c for c in columns if c.primary_key], [c for c in columns if not c.primary_key]


def _keyed(model, rows):
    """Rows as {key: every other column}. A column the row lacks is None, as loaded."""
    keys, values = _split(model)
    return {tuple(r[c.name] for c in keys): tuple(r.get(c.name) for c in values) for r in rows}


def from_api(bootstrap, fixtures):
    """What the load would write for the compared tables, built as the load builds it.

    `clubs` is each player's current club, which the load turns into a club
    spell from the next gameweek on. Once the season is over there is no next
    gameweek and the load starts no spell, so clubs are not compared then: a
    summer transfer would otherwise ask for a load every day until August.
    """
    elements, _ = player_elements(bootstrap)
    api = {
        "gameweeks": _keyed(Gameweek, gameweek_rows("", bootstrap)),
        "fixtures": _keyed(Fixture, fixture_rows("", fixtures)),
        "teams": _keyed(Team, team_rows("", bootstrap)),
        "players": _keyed(SeasonPlayer, season_player_rows("", elements)),
    }
    if any(not e["finished"] for e in bootstrap["events"]):
        api["clubs"] = {(e["id"],): (e["team"],) for e in elements}
    return api


def from_database(connection, season):
    """The same tables as from_api, as the database has them for `season`."""
    found = {}
    for table, model in TABLES.items():
        keys, values = _split(model)
        rows = connection.execute(select(*keys, *values).where(model.season == season))
        found[table] = {tuple(r[: len(keys)]): tuple(r[len(keys) :]) for r in rows}
    # The latest spell is the current club: the load ends every spell at the
    # next one and gives the current club the last.
    latest = (
        select(PlayerClub.element_id, PlayerClub.team_id)
        .where(PlayerClub.season == season)
        .order_by(PlayerClub.element_id, func.lower(PlayerClub.gameweeks).desc())
        .distinct(PlayerClub.element_id)
    )
    found["clubs"] = {(r[0],): (r[1],) for r in connection.execute(latest)}
    return found


def differences(api, database):
    """One line per table where the database disagrees with the API, naming up to SHOWN keys.

    Only keys the API has: the load never deletes, so a row the API has dropped
    stays in the database, and counting it would ask for a load every day.
    """
    found = []
    for table, wanted in api.items():
        have = database.get(table, {})
        keys = sorted(k for k in wanted if wanted[k] != have.get(k))
        if keys:
            shown = ", ".join(str(k[0]) if len(k) == 1 else str(k) for k in keys[:SHOWN])
            more = f" and {len(keys) - SHOWN} more" if len(keys) > SHOWN else ""
            found.append(f"{table}: {len(keys)} differ ({shown}{more})")
    return found


def schema_problem(at, latest):
    """Why loading with this code against a schema at `at` is unsafe, or None."""
    if at == latest:
        return None
    try:
        known = at is not None and ScriptDirectory(str(MIGRATIONS)).get_revision(at)
    except CommandError:
        known = False
    if at is None or known:
        return (
            f"The database schema is at {at or 'nothing'}, and this code needs {latest}. "
            "The deploy migrates it; loading now could fail part-way, so this waits "
            "for the deploy rather than racing it. The next run loads."
        )
    return (
        f"The database schema is at {at}, newer than this code knows ({latest}): main "
        "has moved on since this run checked it out. The next run loads, with that code."
    )


def newest_snapshot():
    """The newest complete snapshot under data/raw/, bootstrap only or not.

    Not LATEST: a bootstrap-only fetch leaves that pointer alone, and the
    bootstrap-only one is the one to compare.
    """
    complete = (
        sorted(p for p in RAW.iterdir() if (p / "manifest.json").exists()) if RAW.is_dir() else []
    )
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
            problem = schema_problem(at, latest)
            if problem:
                print(problem)
                return 1
            print(f"The schema is at {latest}, as this code expects.")
            if schema_only:
                return 0
            snap = newest_snapshot()
            bootstrap = json.loads((snap / "bootstrap.json").read_text())
            fixtures = json.loads((snap / "fixtures.json").read_text())
            season = season_of(bootstrap["events"])
            loaded = connection.scalar(
                select(Snapshot.snapshot_id)
                .where(Snapshot.season == season)
                .order_by(Snapshot.snapshot_id.desc())
                .limit(1)
            )
            if loaded is None:
                found = [f"nothing is loaded for {season}"]
            else:
                api = from_api(bootstrap, fixtures)
                found = differences(api, from_database(connection, season))
            # Recorded whether or not a load follows: the newest check is how
            # fresh production's data is (/v1/status, tools/check_uptime.py).
            connection.execute(
                insert(IngestCheck).values(
                    season=season, compared_snapshot_id=snap.name, load_needed=bool(found)
                )
            )
            connection.commit()
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
    ap.add_argument(
        "--schema-only",
        action="store_true",
        help="only check that the schema is at this code's head",
    )
    args = ap.parse_args(argv)
    require_project_root()
    return main(args.schema_only)


if __name__ == "__main__":
    sys.exit(cli())
