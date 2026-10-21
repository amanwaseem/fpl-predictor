"""Tests for the schema: its migrations, and the constraints that keep rows sound.

Needs a database; see tests/database.py. Locally: make test-db
"""

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import insert, inspect, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError, SAWarning

from fpl.db.models import (
    Base,
    Fixture,
    Gameweek,
    Player,
    PlayerClub,
    PlayerScore,
    Prediction,
    PredictionEntry,
    ScoreEntry,
    Season,
    SeasonPlayer,
    Snapshot,
    Team,
)
from fpl.db.session import make_engine
from tests.database import URL, alembic_config, require_test_database

SEASON = "2026/27"
DEADLINE = datetime(2026, 10, 23, 17, 30, tzinfo=UTC)


def setUpModule():
    require_test_database()


class TestMigrations(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.config = alembic_config()
        cls.engine = make_engine(URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        command.downgrade(self.config, "base")

    def tables(self):
        return set(inspect(self.engine).get_table_names())

    def test_upgrade_creates_every_table(self):
        command.upgrade(self.config, "head")
        self.assertEqual(self.tables(), set(Base.metadata.tables) | {"alembic_version"})

    def test_the_models_match_the_migrations(self):
        """A model changed without a migration would deploy a schema nobody wrote."""
        command.upgrade(self.config, "head")
        with self.engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            self.assertEqual(compare_metadata(context, Base.metadata), [])

    def test_downgrade_removes_every_table(self):
        command.upgrade(self.config, "head")
        command.downgrade(self.config, "base")
        self.assertEqual(self.tables(), {"alembic_version"})


class TestConstraints(unittest.TestCase):
    """Each test runs in a transaction that is rolled back afterwards."""

    @classmethod
    def setUpClass(cls):
        # Rebuilt rather than upgraded: a database already stamped at head
        # would otherwise keep a schema from before an edit to a migration.
        config = alembic_config()
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        cls.engine = make_engine(URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.connection = self.engine.connect()
        self.transaction = self.connection.begin()
        self.insert(Season, season=SEASON)
        self.insert(Team, season=SEASON, team_id=1, code=3, name="Arsenal", short_name="ARS")
        self.insert(Team, season=SEASON, team_id=2, code=7, name="Aston Villa", short_name="AVL")
        self.insert(Player, code=223094, first_name="Bukayo", second_name="Saka")
        self.insert(SeasonPlayer, season=SEASON, element_id=16, player_code=223094,
                    web_name="Saka", position="MID")
        self.insert(Gameweek, season=SEASON, gameweek=8, deadline_utc=DEADLINE)
        self.insert(PredictionEntry, **self.entry())

    def tearDown(self):
        self.transaction.rollback()
        self.connection.close()

    def insert(self, model, **row):
        self.connection.execute(insert(model).values(**row))

    def assertRejected(self, model, **row):
        with self.assertRaises(IntegrityError), self.connection.begin_nested():
            self.insert(model, **row)

    @staticmethod
    def entry(**changes):
        return {"season": SEASON, "gameweek": 8, "model_version": "baseline-v1",
                "snapshot_id": "20261023T080000Z",
                "generated_at_utc": datetime(2026, 10, 23, 8, 5, tzinfo=UTC),
                "deadline_utc": DEADLINE} | changes

    @staticmethod
    def fixture(**changes):
        return {"season": SEASON, "fixture_id": 71, "gameweek": 8,
                "team_h": 1, "team_a": 2} | changes

    def test_a_team_cannot_play_itself(self):
        self.assertRejected(Fixture, **self.fixture(team_a=1))
        self.insert(Fixture, **self.fixture())

    def test_a_score_has_both_sides_or_neither(self):
        self.assertRejected(Fixture, **self.fixture(team_h_score=1))
        self.insert(Fixture, **self.fixture(team_h_score=1, team_a_score=0))

    def test_club_spells_cannot_overlap(self):
        self.insert(PlayerClub, season=SEASON, element_id=16, team_id=1, gameweeks=Range(1, 8))
        self.assertRejected(PlayerClub, season=SEASON, element_id=16, team_id=2,
                            gameweeks=Range(5, 39))
        self.insert(PlayerClub, season=SEASON, element_id=16, team_id=2, gameweeks=Range(8, 39))

    def test_a_club_spell_starts_at_a_gameweek(self):
        for gameweeks in (Range(None, 8), Range(0, 8), Range(empty=True)):
            with self.subTest(gameweeks=gameweeks):
                self.assertRejected(PlayerClub, season=SEASON, element_id=16, team_id=1,
                                    gameweeks=gameweeks)

    def test_player_codes_are_fpls_not_generated(self):
        # SQLAlchemy warns that the key is missing; Postgres then refuses the row.
        with self.assertWarns(SAWarning):
            self.assertRejected(Player, first_name="No", second_name="Code")

    def test_position_is_one_of_four(self):
        self.assertRejected(SeasonPlayer, season=SEASON, element_id=17, player_code=223094,
                            web_name="Saka", position="MGR")

    def test_a_snapshot_id_is_a_timestamp(self):
        self.assertRejected(Snapshot, snapshot_id="latest", season=SEASON,
                            completed_at_utc=DEADLINE, element_count=668, has_players=True)

    def test_an_entry_made_at_or_after_its_deadline_is_refused(self):
        self.assertRejected(PredictionEntry,
                            **self.entry(model_version="late-v1", generated_at_utc=DEADLINE))

    def test_a_model_version_has_no_underscore(self):
        self.assertRejected(PredictionEntry, **self.entry(model_version="baseline_v2"))

    def test_a_prediction_belongs_to_an_entry(self):
        row = {"season": SEASON, "gameweek": 8, "player_id": 16, "web_name": "Saka",
               "team": "ARS", "position": "MID", "price": Decimal("10.1"),
               "predicted_points": Decimal("5.12"), "expected_minutes": Decimal("85.0"),
               "points_per_90": Decimal("5.42"), "n_fixtures": 1, "status": "a"}
        self.assertRejected(Prediction, **row, model_version="unlogged-v1")
        self.insert(Prediction, **row, model_version="baseline-v1")

    def test_the_log_copy_keeps_every_digit_it_was_written_with(self):
        """fpl.log does not limit decimal places, so neither may its copy (#80)."""
        self.insert(Prediction, season=SEASON, gameweek=8, model_version="baseline-v1",
                    player_id=16, web_name="Saka", team="ARS", position="MID",
                    price=Decimal("10.1"), predicted_points=Decimal("5.125"),
                    expected_minutes=Decimal("85.25"), points_per_90=Decimal("5.4321"),
                    n_fixtures=1, status="a")
        stored = self.connection.execute(
            select(Prediction.predicted_points, Prediction.expected_minutes,
                   Prediction.points_per_90)).one()
        self.assertEqual(tuple(stored), (Decimal("5.125"), Decimal("85.25"), Decimal("5.4321")))

    def test_a_score_error_is_computed_by_postgres(self):
        self.insert(ScoreEntry, season=SEASON, gameweek=8, model_version="baseline-v1",
                    harness_version="harness-v1", actuals_snapshot_id="20261027T080000Z",
                    n=668, mae=1.03, rmse=2.06, bias=-0.17, spearman=0.74, summary={})
        self.insert(PlayerScore, season=SEASON, gameweek=8, model_version="baseline-v1",
                    player_id=16, predicted_points=Decimal("6.94"), actual_points=6,
                    expected_minutes=Decimal("90.0"), actual_minutes=90)
        error = self.connection.scalar(select(PlayerScore.error))
        self.assertEqual(error, Decimal("0.94"))


if __name__ == "__main__":
    unittest.main()
