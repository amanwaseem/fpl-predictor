"""Build the feature table, player_gameweek_features, from the database (#84).

    fpl-features                      # what is missing or out of date
    fpl-features --rebuild            # every season, from scratch
    fpl-features --season 2024/25     # one season (repeatable)

Reads each season from the football tables into a SeasonData and builds its
gameweeks with fpl.models.feature_table, whose rules say what a row may know.
A season's gameweeks are those it can build: every one up to the first not yet
settled, which for a past season is all 38, and for the live one is the
settled rounds and the gameweek coming next.

By default a season is built when what it is built from has changed since
its last build (fpl.db.models.FeatureBuild): a round settling, a new
snapshot, a backfill, a committed entry, the season before it, or the feature
version. Each season is written in one transaction.

Run from the repository root, with DATABASE_URL set.
"""

import argparse
import hashlib
import sys
from collections import defaultdict

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from fpl.db.load import LAST_GAMEWEEK, LOAD_LOCK
from fpl.db.models import (
    FeatureBuild,
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


def season_before(season):
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
    before = season_before(season)
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


def fingerprint(connection, season):
    """A digest of everything `season`'s rows are built from, and the version they are
    built by: its results, fixtures, clubs, settled rounds and committed entries,
    and the season before it, whose totals are the last-season columns."""
    stats, before = PlayerFixtureStats, season_before(season)
    parts = [FEATURE_VERSION]
    for s in (season, before):
        parts.append(
            tuple(
                connection.execute(
                    select(
                        func.count(),
                        func.sum(stats.minutes),
                        func.sum(stats.total_points),
                        func.sum(stats.bonus),
                        func.sum(stats.value),
                        func.sum(func.coalesce(stats.expected_goals, 0)),
                        func.sum(func.coalesce(stats.expected_assists, 0)),
                        func.sum(stats.element_id * stats.fixture_id),
                    ).where(stats.season == s)
                ).one()
            )
        )
    parts.append(
        tuple(
            connection.execute(
                select(
                    func.count(),
                    func.sum(Fixture.gameweek * Fixture.fixture_id),
                    func.sum(
                        func.coalesce(Fixture.team_h_score, -1) * 31
                        + func.coalesce(Fixture.team_a_score, -1)
                    ),
                    func.sum(Fixture.team_h * 37 + Fixture.team_a),
                ).where(Fixture.season == season)
            ).one()
        )
    )
    parts.append(
        sorted(
            connection.scalars(
                select(Gameweek.gameweek).where(
                    Gameweek.season == season, Gameweek.finished, Gameweek.data_checked
                )
            )
        )
    )
    parts.append(
        tuple(
            connection.execute(
                select(
                    func.count(),
                    func.sum(PlayerClub.element_id * PlayerClub.team_id),
                    func.sum(func.lower(PlayerClub.gameweeks)),
                ).where(PlayerClub.season == season)
            ).one()
        )
    )
    parts.append(
        tuple(
            connection.execute(
                select(func.count(), func.sum(Prediction.gameweek * Prediction.player_id)).where(
                    Prediction.season == season
                )
            ).one()
        )
    )
    return hashlib.sha256(repr(parts).encode()).hexdigest()


def build_season(engine, season):
    """Replace `season`'s feature rows, and record what they were built from.
    Returns (gameweeks built, rows)."""
    with engine.begin() as connection:
        # The snapshot loader's lock, which the backfill takes too: what is read
        # here cannot change under the build, and a backfill cannot delete the
        # season's players while its features are being written.
        connection.execute(select(func.pg_advisory_xact_lock(LOAD_LOCK)))
        data = read_season(connection, season)
        gameweeks = buildable(data)
        rows = [row for gw in gameweeks for row in build_gameweek(data, gw)]
        connection.execute(
            delete(PlayerGameweekFeatures).where(PlayerGameweekFeatures.season == season)
        )
        if rows:
            connection.execute(insert(PlayerGameweekFeatures), rows)
        values = {
            "feature_version": FEATURE_VERSION,
            "inputs": fingerprint(connection, season),
            "rows": len(rows),
            "built_at_utc": func.now(),
        }
        connection.execute(
            insert(FeatureBuild)
            .values(season=season, **values)
            .on_conflict_do_update(index_elements=["season"], set_=values)
        )
    return gameweeks, len(rows)


def stale_seasons(connection, seasons):
    """The seasons to build by default: any whose inputs, or the feature version, have
    changed since it was built, and any never built."""
    built = dict(connection.execute(select(FeatureBuild.season, FeatureBuild.inputs)).all())
    return [s for s in seasons if built.get(s) != fingerprint(connection, s)]


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
            print(
                f"Every season is built, at {FEATURE_VERSION}, from what is there; nothing to do."
            )
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
