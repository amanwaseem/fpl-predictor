"""fpl-backfill against Postgres (#79).

The synthetic archive (tests/history_files.py) always; the real one, all ten
seasons, when its pinned files are under data/raw/history/ (CI's db job
downloads them first). Needs a database; see tests/database.py.
"""

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from alembic import command
from sqlalchemy import func, insert, select

from fpl.db import backfill
from fpl.db.models import Player, PlayerFixtureStats, Season, SeasonPlayer, Snapshot
from fpl.db.session import make_engine
from fpl.ingest import history
from tests.database import ROOT, URL, alembic_config, empty_every_table, require_test_database
from tests.history_files import season_rows, write_season

REAL = ROOT / history.RAW


def setUpModule():
    require_test_database()


class BackfillCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        command.upgrade(alembic_config(), "head")
        cls.engine = make_engine(URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        empty_every_table(self.engine)
        self.addCleanup(empty_every_table, self.engine)

    def count(self, model, season):
        with self.engine.connect() as c:
            return c.scalar(select(func.count()).select_from(model).where(model.season == season))


class TestSyntheticBackfill(BackfillCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.raw = Path(tmp.name)
        write_season(self.raw, "2016/17", teams_csv=True)

    def run_backfill(self, expected=None):
        with unittest.mock.patch("builtins.print"):
            return backfill.backfill(
                self.engine, ["2016/17"], self.raw, expected or {}, verified=True
            )

    def test_a_season_loads_whole(self):
        self.assertEqual(
            self.run_backfill(),
            {
                "2016/17": {
                    "teams": 2,
                    "season_players": 3,
                    "gameweeks": 38,
                    "fixtures": 2,
                    "player_clubs": 4,
                    "player_fixture_stats": 6,
                }
            },
        )

    def test_loading_again_replaces_rather_than_adds(self):
        self.run_backfill()
        self.run_backfill()
        self.assertEqual(self.count(PlayerFixtureStats, "2016/17"), 6)

    def test_a_count_that_differs_from_the_pinned_one_keeps_nothing(self):
        with self.assertRaises(SystemExit):
            self.run_backfill({"2016/17": {"player_fixture_stats": 7}})
        self.assertEqual(self.count(SeasonPlayer, "2016/17"), 0)

    def test_the_newest_season_names_a_person_whatever_the_order(self):
        players, gws = season_rows()
        renamed = [dict(p, first_name="Later") if p["id"] == 1 else p for p in players]
        write_season(self.raw, "2017/18", players=renamed, teams_csv=True)
        for order in (["2016/17", "2017/18"], ["2017/18", "2016/17"]):
            with self.subTest(order=order), unittest.mock.patch("builtins.print"):
                empty_every_table(self.engine)
                backfill.backfill(self.engine, order, self.raw, {}, verified=True)
                with self.engine.connect() as c:
                    name = c.scalar(select(Player.first_name).where(Player.code == 1001))
                self.assertEqual(name, "Later")

    def test_files_are_checked_unless_already_verified(self):
        with self.assertRaises(SystemExit):
            backfill.backfill(self.engine, ["2016/17"], self.raw, {})

    def test_the_live_season_is_refused(self):
        from datetime import UTC, datetime

        with self.engine.begin() as c:
            c.execute(insert(Season).values(season="2016/17"))
            c.execute(
                insert(Snapshot).values(
                    snapshot_id="20160901T000000Z",
                    season="2016/17",
                    element_count=1,
                    has_players=True,
                    completed_at_utc=datetime(2016, 9, 1, tzinfo=UTC),
                )
            )
        with self.assertRaises(SystemExit):
            self.run_backfill()


@unittest.skipUnless(
    all((REAL / name).exists() for name in history.FILES),
    "the archive is not under data/raw/history/: fpl-backfill --download-only",
)
class TestTheRealArchive(BackfillCase):
    """All ten seasons, against the counts pinned in fpl.db.backfill.EXPECTED."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        empty_every_table(cls.engine)
        cwd = os.getcwd()
        os.chdir(ROOT)
        try:
            with unittest.mock.patch("builtins.print"):
                cls.loaded = backfill.backfill(cls.engine, history.SEASONS)
        finally:
            os.chdir(cwd)

    def setUp(self):
        pass  # one load for the class; emptied after it

    @classmethod
    def tearDownClass(cls):
        empty_every_table(cls.engine)
        super().tearDownClass()

    def test_every_season_has_its_pinned_counts(self):
        self.assertEqual(self.loaded, backfill.EXPECTED)

    def test_a_long_serving_player_has_his_whole_history_across_seasons(self):
        """Salah, by FPL's code for him: element ids change every season, the code does not."""
        with self.engine.connect() as c:
            rows = c.execute(
                select(SeasonPlayer.season, func.sum(PlayerFixtureStats.total_points))
                .join(
                    PlayerFixtureStats,
                    (PlayerFixtureStats.season == SeasonPlayer.season)
                    & (PlayerFixtureStats.element_id == SeasonPlayer.element_id),
                )
                .where(SeasonPlayer.player_code == 118748)
                .group_by(SeasonPlayer.season)
                .order_by(SeasonPlayer.season)
            ).all()
        self.assertEqual([season for season, _ in rows], history.SEASONS[1:])
        # His FPL totals: 303 in 2017/18, 344 in 2024/25.
        points = dict(rows)
        self.assertEqual((points["2017/18"], points["2024/25"]), (303, 344))


if __name__ == "__main__":
    unittest.main()
