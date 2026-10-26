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

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

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
from fpl.ingest import history
from fpl.paths import require_project_root

# Rows per season once loaded: (season_players, fixtures, player_fixture_stats),
# counted from the pinned files when they were first loaded.
EXPECTED: dict[str, tuple[int, int, int]] = {
    "2016/17": (683, 380, 23679),
    "2017/18": (647, 380, 22467),
    "2018/19": (624, 380, 21790),
    "2019/20": (666, 380, 22501),
    "2020/21": (713, 380, 24365),
    "2021/22": (737, 380, 25447),
    "2022/23": (778, 380, 26505),
    "2023/24": (865, 380, 29725),
    "2024/25": (784, 380, 27283),
    "2025/26": (841, 380, 29747),
}

# Children before parents, so a season can be deleted in this order.
TABLES = [PlayerFixtureStats, PlayerClub, Fixture, Gameweek, SeasonPlayer, Team]
LOCK = 79
# Rows per INSERT: psycopg sends each as one statement of parameters, and a
# season's 30,000 stats rows at once is more than it needs to be.
BATCH = 5000


def load_season(connection, parsed):
    """Replace `parsed.season`'s rows with `parsed`'s. Raises SystemExit for the live season."""
    season = parsed.season
    if connection.scalar(select(Snapshot.snapshot_id).where(Snapshot.season == season).limit(1)):
        raise SystemExit(
            f"{season} has snapshots loaded: it is the live season, which "
            "fpl-load keeps, not a past one to backfill."
        )
    connection.execute(insert(Season).values(season=season).on_conflict_do_nothing())
    for model in TABLES:
        connection.execute(delete(model).where(model.season == season))
    # People are not per season; a name already known, from a later season or
    # the live one, is kept.
    connection.execute(insert(Player).on_conflict_do_nothing(), parsed.players)
    for model, rows in (
        (Team, parsed.teams),
        (SeasonPlayer, parsed.season_players),
        (Gameweek, parsed.gameweeks),
        (Fixture, parsed.fixtures),
        (PlayerClub, parsed.player_clubs),
        (PlayerFixtureStats, parsed.player_fixture_stats),
    ):
        for start in range(0, len(rows), BATCH):
            connection.execute(insert(model), rows[start : start + BATCH])


def counts(connection, season):
    return tuple(
        connection.scalar(select(func.count()).select_from(model).where(model.season == season))
        for model in (SeasonPlayer, Fixture, PlayerFixtureStats)
    )


def backfill(engine, seasons, raw=history.RAW, expected=None):
    """Load `seasons` from `raw`. Returns {season: counts}."""
    expected = EXPECTED if expected is None else expected
    names = history.short_names(raw)
    loaded = {}
    for season in seasons:
        parsed = history.parse_season(season, raw, names)
        with engine.begin() as connection:
            connection.execute(select(func.pg_advisory_xact_lock(LOCK)))
            load_season(connection, parsed)
            found = counts(connection, season)
            want = expected.get(season)
            if want is not None and found != want:
                raise SystemExit(
                    f"{season}: loaded {found} (players, fixtures, stats), "
                    f"expected {want}. Nothing for it was kept."
                )
        loaded[season] = found
        print(
            f"{season}: {found[0]} players, {found[1]} fixtures, {found[2]} player-fixture "
            f"rows ({parsed.duplicates} duplicate rows dropped, {parsed.not_players} "
            "rows for managers, who are not players)"
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
    history.verify()
    engine = make_engine()
    try:
        backfill(engine, seasons)
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
