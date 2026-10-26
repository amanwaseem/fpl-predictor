"""fpl-features against Postgres (#84): the table, and the leak test through the pipeline.

tests/test_feature_table.py poisons the builder's input; this poisons the
database itself, after the cut, and requires the rebuilt rows for the
gameweek to be the same. With the real archive under data/raw/history/ it
also builds all ten past seasons. Needs a database; see tests/database.py.
"""

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from alembic import command
from sqlalchemy import func, select, update

from fpl.db import backfill, features
from fpl.db.models import Fixture, PlayerFixtureStats, PlayerGameweekFeatures
from fpl.db.session import make_engine
from fpl.ingest import history
from fpl.models import feature_table
from tests.database import ROOT, URL, alembic_config, empty_every_table, require_test_database
from tests.history_files import write_season

REAL = ROOT / history.RAW


def setUpModule():
    require_test_database()


class FeaturesCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        command.upgrade(alembic_config(), "head")
        cls.engine = make_engine(URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def rows(self, season, gameweek):
        with self.engine.connect() as c:
            return [
                dict(r._mapping)
                for r in c.execute(
                    select(PlayerGameweekFeatures)
                    .where(
                        PlayerGameweekFeatures.season == season,
                        PlayerGameweekFeatures.gameweek == gameweek,
                    )
                    .order_by(PlayerGameweekFeatures.element_id)
                )
            ]

    def poison_from(self, season, gameweek):
        """Every result at or after `gameweek`, in the database itself."""
        later = select(Fixture.fixture_id).where(
            Fixture.season == season, Fixture.gameweek >= gameweek
        )
        with self.engine.begin() as c:
            c.execute(
                update(PlayerFixtureStats)
                .where(
                    PlayerFixtureStats.season == season,
                    PlayerFixtureStats.fixture_id.in_(later),
                )
                .values(minutes=90, total_points=99, bonus=3, value=999, goals_scored=9)
            )
            c.execute(
                update(Fixture)
                .where(Fixture.season == season, Fixture.gameweek >= gameweek)
                .values(team_h_score=9, team_a_score=0)
            )


class TestSyntheticSeason(FeaturesCase):
    def setUp(self):
        empty_every_table(self.engine)
        self.addCleanup(empty_every_table, self.engine)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        raw = Path(tmp.name)
        write_season(raw, "2016/17", teams_csv=True)
        with unittest.mock.patch("builtins.print"):
            backfill.backfill(self.engine, ["2016/17"], raw, {}, verified=True)

    def test_a_season_builds_a_row_per_player_per_gameweek_with_fixtures(self):
        gameweeks, count = features.build_season(self.engine, "2016/17")
        self.assertEqual(gameweeks, [1, 2])
        self.assertEqual(count, 6)
        self.assertEqual({r["element_id"] for r in self.rows("2016/17", 2)}, {1, 2, 3})

    def test_poisoning_the_database_after_the_cut_changes_nothing(self):
        features.build_season(self.engine, "2016/17")
        clean = self.rows("2016/17", 2)
        self.poison_from("2016/17", 2)
        features.build_season(self.engine, "2016/17")
        self.assertEqual(self.rows("2016/17", 2), clean)

    def test_the_poison_would_have_been_noticed(self):
        features.build_season(self.engine, "2016/17")
        clean = self.rows("2016/17", 2)
        self.poison_from("2016/17", 1)
        features.build_season(self.engine, "2016/17")
        self.assertNotEqual(self.rows("2016/17", 2), clean)

    def test_a_built_past_season_is_left_alone_until_the_version_changes(self):
        features.build_season(self.engine, "2016/17")
        with self.engine.connect() as c:
            self.assertEqual(features.stale_seasons(c, ["2016/17"]), [])
            with unittest.mock.patch.object(features, "FEATURE_VERSION", "features-v2"):
                self.assertEqual(features.stale_seasons(c, ["2016/17"]), ["2016/17"])

    def test_backfilling_a_season_again_clears_its_features(self):
        features.build_season(self.engine, "2016/17")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        write_season(Path(tmp.name), "2016/17", teams_csv=True)
        with unittest.mock.patch("builtins.print"):
            backfill.backfill(self.engine, ["2016/17"], Path(tmp.name), {}, verified=True)
        self.assertEqual(self.rows("2016/17", 2), [])


@unittest.skipUnless(
    all((REAL / name).exists() for name in history.FILES),
    "the archive is not under data/raw/history/: fpl-backfill --download-only",
)
class TestTheRealArchive(FeaturesCase):
    """All ten past seasons; and the leak test on one with expected goals."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        empty_every_table(cls.engine)
        cwd = os.getcwd()
        os.chdir(ROOT)
        try:
            with unittest.mock.patch("builtins.print"):
                backfill.backfill(cls.engine, history.SEASONS)
            cls.built = {s: features.build_season(cls.engine, s) for s in history.SEASONS}
        finally:
            os.chdir(cwd)

    @classmethod
    def tearDownClass(cls):
        empty_every_table(cls.engine)
        super().tearDownClass()

    def test_every_past_season_has_every_gameweek_with_fixtures(self):
        """All 38, but for 2022/23's GW7, postponed whole: no fixture, no rows."""
        for season, (gameweeks, count) in self.built.items():
            blank = [7] if season == "2022/23" else []
            with self.subTest(season=season):
                self.assertEqual(gameweeks, [g for g in range(1, 39) if g not in blank])
                self.assertGreater(count, 20_000)

    def test_expected_goals_are_there_from_2022_23_and_not_before(self):
        with self.engine.connect() as c:
            seasons = set(
                c.scalars(
                    select(PlayerGameweekFeatures.season)
                    .where(PlayerGameweekFeatures.xg_per90_5.is_not(None))
                    .distinct()
                )
            )
        self.assertEqual(seasons, {"2022/23", "2023/24", "2024/25", "2025/26"})

    def test_poisoning_a_real_season_after_the_cut_changes_nothing(self):
        season, cut = "2023/24", 20
        clean = self.rows(season, cut)
        try:
            self.poison_from(season, cut)
            features.build_season(self.engine, season)
            self.assertEqual(self.rows(season, cut), clean)
            self.assertNotEqual(self.rows(season, cut + 1), [])
        finally:
            cwd = os.getcwd()
            os.chdir(ROOT)
            try:
                with unittest.mock.patch("builtins.print"):
                    backfill.backfill(self.engine, [season])
                features.build_season(self.engine, season)
            finally:
                os.chdir(cwd)

    def test_every_row_is_this_version(self):
        with self.engine.connect() as c:
            versions = set(c.scalars(select(PlayerGameweekFeatures.feature_version).distinct()))
            total = c.scalar(select(func.count()).select_from(PlayerGameweekFeatures))
        self.assertEqual(versions, {feature_table.FEATURE_VERSION})
        self.assertEqual(total, sum(count for _, count in self.built.values()))
