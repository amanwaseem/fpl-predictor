"""Load past seasons into the database as training data (#79, ADR 0018).

    fpl-backfill --download-only            # fetch and check the pinned files
    fpl-backfill                            # every season, 2016/17 to 2025/26
    fpl-backfill --season 2023/24           # one

Each season is written in one transaction, replacing whatever the database
held for it, so running it again changes nothing and an interrupted run
leaves every season whole or absent. A season that has snapshots loaded is
the live one, the snapshot loader's, and is refused.

After loading, the rows per season are checked against EXPECTED, counted
once from the pinned files: a different count means the parsing changed, and
the run fails rather than leave a season that differs from the last.

Run from the repository root, with DATABASE_URL set.
"""

import argparse
import sys

from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert

from fpl.db.load import LAST_GAMEWEEK, LOAD_LOCK
from fpl.db.models import (
    FeatureBuild,
    Fixture,
    Gameweek,
    Player,
    PlayerClub,
    PlayerFixtureStats,
    PlayerGameweekFeatures,
    Season,
    SeasonPlayer,
    Snapshot,
    Team,
)
from fpl.db.session import exits_on_bad_url, make_engine
from fpl.ingest import history
from fpl.paths import require_project_root

# Rows per season once loaded, for every table the backfill writes, counted
# from the pinned files when they were first loaded: a change to any of its
# parsing, or to the builders it shares with the snapshot loader, shows up.
# Every season has 20 clubs, 38 gameweeks and 380 fixtures; the rest differ.
EVERY_SEASON = {"teams": 20, "gameweeks": LAST_GAMEWEEK, "fixtures": 380}
EXPECTED: dict[str, dict[str, int]] = {
    season: EVERY_SEASON | counts
    for season, counts in {
        "2016/17": {"season_players": 683, "player_clubs": 718, "player_fixture_stats": 23679},
        "2017/18": {"season_players": 647, "player_clubs": 674, "player_fixture_stats": 22467},
        "2018/19": {"season_players": 624, "player_clubs": 628, "player_fixture_stats": 21790},
        "2019/20": {"season_players": 666, "player_clubs": 674, "player_fixture_stats": 22501},
        "2020/21": {"season_players": 713, "player_clubs": 726, "player_fixture_stats": 24365},
        "2021/22": {"season_players": 737, "player_clubs": 751, "player_fixture_stats": 25447},
        "2022/23": {"season_players": 778, "player_clubs": 807, "player_fixture_stats": 26505},
        "2023/24": {"season_players": 865, "player_clubs": 890, "player_fixture_stats": 29725},
        "2024/25": {"season_players": 784, "player_clubs": 815, "player_fixture_stats": 27283},
        "2025/26": {"season_players": 841, "player_clubs": 870, "player_fixture_stats": 29747},
    }.items()
}

# Derived from the rest and rebuilt by fpl-features (#84): cleared first, with
# the record of their build, so fpl-features sees the season as unbuilt.
DERIVED = [PlayerGameweekFeatures, FeatureBuild]
# Children before parents, so a season can be deleted in this order.
TABLES = [PlayerFixtureStats, PlayerClub, Fixture, Gameweek, SeasonPlayer, Team]
COUNTED = {model.__tablename__: model for model in reversed(TABLES)}


def load_season(connection, parsed):
    """Replace `parsed.season`'s rows with `parsed`'s. Raises SystemExit for the live season."""
    season = parsed.season
    if connection.scalar(select(Snapshot.snapshot_id).where(Snapshot.season == season).limit(1)):
        raise SystemExit(
            f"{season} has snapshots loaded: it is the live season, which "
            "fpl-load keeps, not a past one to backfill."
        )
    connection.execute(insert(Season).values(season=season).on_conflict_do_nothing())
    for model in DERIVED + TABLES:
        connection.execute(delete(model).where(model.season == season))
    # People are not per season. A name is taken from this season unless a
    # later one, past or live, already has the person: the newest name wins,
    # whatever order the seasons are loaded in.
    # player_rows lists each person once, as one statement cannot update a row
    # twice.
    statement = insert(Player)
    later = exists().where(
        SeasonPlayer.player_code == statement.excluded.code, SeasonPlayer.season > season
    )
    if parsed.players:
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=["code"],
                set_={
                    "first_name": statement.excluded.first_name,
                    "second_name": statement.excluded.second_name,
                },
                where=~later,
            ),
            parsed.players,
        )
    for model, rows in (
        (Team, parsed.teams),
        (SeasonPlayer, parsed.season_players),
        (Gameweek, parsed.gameweeks),
        (Fixture, parsed.fixtures),
        (PlayerClub, parsed.player_clubs),
        (PlayerFixtureStats, parsed.player_fixture_stats),
    ):
        # SQLAlchemy pages a many-row INSERT itself (insertmanyvalues).
        if rows:
            connection.execute(insert(model), rows)


def counts(connection, season):
    """{table: rows for `season`}, in one query."""
    row = connection.execute(
        select(
            *(
                select(func.count())
                .select_from(model)
                .where(model.season == season)
                .scalar_subquery()
                .label(name)
                for name, model in COUNTED.items()
            )
        )
    ).one()
    return dict(row._mapping)


def backfill(engine, seasons, raw=history.RAW, expected=None, verified=False):
    """Load `seasons` from `raw`, checking its files' hashes first unless `verified`.
    Returns {season: counts}."""
    expected = EXPECTED if expected is None else expected
    if not verified:
        history.verify(raw)
    names = history.short_names(raw)
    loaded = {}
    for season in seasons:
        parsed = history.parse_season(season, raw, names)
        with engine.begin() as connection:
            # The snapshot loader's lock: the check that a season has no
            # snapshots, then its delete, must not interleave with an fpl-load,
            # and two backfills take it one at a time too.
            connection.execute(select(func.pg_advisory_xact_lock(LOAD_LOCK)))
            load_season(connection, parsed)
            found = counts(connection, season)
            want = expected.get(season)
            if want is not None and found != want:
                differ = {k: (found[k], want.get(k)) for k in found if found[k] != want.get(k)}
                raise SystemExit(
                    f"{season}: loaded rows differ from the pinned counts, as (loaded, "
                    f"pinned): {differ}. Nothing for it was kept."
                )
        loaded[season] = found
        print(
            f"{season}: {found['season_players']} players, {found['fixtures']} fixtures, "
            f"{found['player_fixture_stats']} player-fixture rows ({parsed.duplicates} "
            f"duplicate rows dropped, {parsed.not_players} rows for managers, who are not "
            "players)"
        )
    return loaded


@exits_on_bad_url
def main(seasons, download_only=False):
    fetched = history.download()
    print(
        f"{history.SOURCE}@{history.COMMIT[:7]}: {len(history.FILES)} files checked, "
        f"{len(fetched)} downloaded, under {history.RAW}"
    )
    if download_only:
        return 0
    engine = make_engine()
    try:
        # download() has just checked every file's hash.
        backfill(engine, seasons, verified=True)
    finally:
        engine.dispose()
    return 0


def cli(argv=None):
    """`fpl-backfill`, or `python -m fpl.db.backfill`."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--season",
        action="append",
        choices=history.SEASONS,
        help="a season to load (repeatable; default: all of them)",
    )
    ap.add_argument(
        "--download-only",
        action="store_true",
        help="fetch and check the pinned files, and load nothing",
    )
    args = ap.parse_args(argv)
    require_project_root()
    return main(args.season or history.SEASONS, args.download_only)


if __name__ == "__main__":
    sys.exit(cli())
