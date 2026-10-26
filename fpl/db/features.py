"""Build the feature table, player_gameweek_features, from the database (#84).

    fpl-features                      # what is missing or out of date
    fpl-features --rebuild            # every season, from scratch
    fpl-features --season 2024/25     # one season (repeatable)

Reads each season from the football tables into a SeasonData and builds its
gameweeks with fpl.models.feature_table, whose rules say what a row may know.
A season's gameweeks are those it can build: every one up to the first not yet
settled, which for a past season is all 38, and for the live one is the
settled rounds and the gameweek coming next.

By default a past season is built once, and again only when the feature
version changes; the live season is rebuilt every time, since a round settling
or a new snapshot changes it. Each season is written in one transaction.

Run from the repository root, with DATABASE_URL set.
"""

import argparse
import sys
from collections import defaultdict

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from fpl.db.load import LAST_GAMEWEEK
from fpl.db.models import (
    Fixture,
    Gameweek,
    PlayerClub,
    PlayerFixtureStats,
    PlayerGameweekFeatures,
    Prediction,
    Season,
    SeasonPlayer,
)
from fpl.db.session import exits_on_bad_url, make_engine
from fpl.models.feature_table import FEATURE_VERSION, SeasonData, build_gameweek
from fpl.paths import require_project_root

LOCK = 84


def previous_season(season):
    """'2025/26' for '2026/27'."""
    start = int(season[:4]) - 1
    return f"{start}/{(start + 1) % 100:02d}"


def read_season(connection, season):
    """A SeasonData for `season`, from the football tables and the log."""
    fixtures = [
        {
            "id": r.fixture_id,
            "event": r.gameweek,
            "team_h": r.team_h,
            "team_a": r.team_a,
            "team_h_score": r.team_h_score,
            "team_a_score": r.team_a_score,
            "finished": r.finished,
        }
        for r in connection.execute(select(Fixture).where(Fixture.season == season))
    ]
    stats = PlayerFixtureStats
    histories = defaultdict(list)
    for r in connection.execute(
        select(
            stats.element_id,
            Fixture.gameweek,
            stats.fixture_id,
            stats.was_home,
            stats.minutes,
            stats.total_points,
            stats.bonus,
            stats.value,
            stats.expected_goals,
            stats.expected_assists,
        )
        .join(Fixture, (Fixture.season == stats.season) & (Fixture.fixture_id == stats.fixture_id))
        .where(stats.season == season)
        .order_by(stats.element_id, Fixture.gameweek, stats.fixture_id)
    ):
        histories[r.element_id].append(
            {
                "round": r.gameweek,
                "fixture": r.fixture_id,
                "was_home": r.was_home,
                "minutes": r.minutes,
                "total_points": r.total_points,
                "bonus": r.bonus,
                "value": r.value,
                "expected_goals": r.expected_goals,
                "expected_assists": r.expected_assists,
            }
        )
    settled = set(
        connection.scalars(
            select(Gameweek.gameweek).where(
                Gameweek.season == season, Gameweek.finished, Gameweek.data_checked
            )
        )
    )
    clubs = defaultdict(list)
    for r in connection.execute(
        select(PlayerClub)
        .where(PlayerClub.season == season)
        .order_by(PlayerClub.element_id, PlayerClub.gameweeks)
    ):
        upper = LAST_GAMEWEEK if r.gameweeks.upper is None else r.gameweeks.upper - 1
        clubs[r.element_id].append((r.team_id, r.gameweeks.lower, upper))
    players = {
        r.element_id: {"position": r.position, "code": r.player_code}
        for r in connection.execute(select(SeasonPlayer).where(SeasonPlayer.season == season))
    }
    before = previous_season(season)
    previous = {
        r.player_code: {"minutes": r.minutes, "points": r.points}
        for r in connection.execute(
            select(
                SeasonPlayer.player_code,
                func.sum(stats.minutes).label("minutes"),
                func.sum(stats.total_points).label("points"),
            )
            .join(
                stats,
                (stats.season == SeasonPlayer.season)
                & (stats.element_id == SeasonPlayer.element_id),
            )
            .where(SeasonPlayer.season == before)
            .group_by(SeasonPlayer.player_code)
        )
    }
    # Every committed entry for the gameweek was made from one snapshot before
    # its deadline; where models disagree, the first by name is taken.
    status = {}
    for r in connection.execute(
        select(Prediction.gameweek, Prediction.player_id, Prediction.status)
        .where(Prediction.season == season)
        .order_by(Prediction.model_version.desc())
    ):
        status[(r.gameweek, r.player_id)] = r.status
    return SeasonData(
        season=season,
        fixtures=fixtures,
        histories=dict(histories),
        settled=settled,
        clubs=dict(clubs),
        players=players,
        previous=previous,
        status=status,
    )


def buildable(data):
    """The gameweeks `data` can build: those with fixtures, up to the first unsettled."""
    unsettled = [g for g in range(1, LAST_GAMEWEEK + 1) if g not in data.settled]
    last = unsettled[0] if unsettled else LAST_GAMEWEEK
    with_fixtures = {f["event"] for f in data.fixtures if f.get("event") is not None}
    return [g for g in range(1, last + 1) if g in with_fixtures]


def build_season(engine, season):
    """Replace `season`'s feature rows. Returns (gameweeks built, rows)."""
    with engine.begin() as connection:
        connection.execute(select(func.pg_advisory_xact_lock(LOCK)))
        data = read_season(connection, season)
        gameweeks = buildable(data)
        rows = [row for gw in gameweeks for row in build_gameweek(data, gw)]
        connection.execute(
            delete(PlayerGameweekFeatures).where(PlayerGameweekFeatures.season == season)
        )
        if rows:
            connection.execute(insert(PlayerGameweekFeatures), rows)
    return gameweeks, len(rows)


def stale_seasons(connection, seasons):
    """The seasons to build by default: the live one, and any past one not built at this
    version. A season is live while any of its gameweeks is unsettled."""
    wanted = []
    for season in seasons:
        live = connection.scalar(
            select(func.count())
            .select_from(Gameweek)
            .where(Gameweek.season == season, ~(Gameweek.finished & Gameweek.data_checked))
        )
        versions = set(
            connection.scalars(
                select(PlayerGameweekFeatures.feature_version)
                .distinct()
                .where(PlayerGameweekFeatures.season == season)
            )
        )
        if live or versions != {FEATURE_VERSION}:
            wanted.append(season)
    return wanted


@exits_on_bad_url
def main(seasons=None, rebuild=False):
    engine = make_engine()
    try:
        with engine.connect() as connection:
            known = list(connection.scalars(select(Season.season).order_by(Season.season)))
            chosen = seasons or known
            missing = sorted(set(chosen) - set(known))
            if missing:
                raise SystemExit(f"Not in the database: {', '.join(missing)}.")
            if not rebuild and not seasons:
                chosen = stale_seasons(connection, chosen)
        if not chosen:
            print(f"Every season is built at {FEATURE_VERSION}; nothing to do.")
        for season in chosen:
            gameweeks, count = build_season(engine, season)
            span = f"GW{gameweeks[0]}-{gameweeks[-1]}" if gameweeks else "no gameweeks"
            print(f"{season}: {count} rows, {span}, {FEATURE_VERSION}")
    finally:
        engine.dispose()
    return 0


def cli(argv=None):
    """`fpl-features`, or `python -m fpl.db.features`."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--season", action="append", help="a season to build (repeatable)")
    ap.add_argument(
        "--rebuild", action="store_true", help="every season, whether or not it is out of date"
    )
    args = ap.parse_args(argv)
    require_project_root()
    return main(args.season, args.rebuild)


if __name__ == "__main__":
    sys.exit(cli())
