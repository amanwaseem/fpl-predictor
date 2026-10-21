"""Tests for fpl-project and fpl-reconcile: the log into Postgres, and checking it.

Needs a database; see tests/database.py. Locally: make test-db
"""

import os
from decimal import Decimal

from alembic import command
from sqlalchemy import delete, func, insert, select, update

from fpl.db.models import PlayerScore, Prediction, PredictionEntry
from fpl.db.project import project, read_log
from fpl.db.reconcile import differences
from fpl.db.session import make_engine
from tests.database import ROOT, URL, alembic_config, empty_every_table, require_test_database
from tests.fixtures import TempCwd, read_csv, write_csv
from tests.log_files import write_entry, write_score


def setUpModule():
    require_test_database()


class DatabaseCase(TempCwd):
    """A working directory of its own, and empty log tables before and after."""

    @classmethod
    def setUpClass(cls):
        command.upgrade(alembic_config(), "head")
        cls.engine = make_engine(URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        super().setUp()
        empty_every_table(self.engine)

    def tearDown(self):
        empty_every_table(self.engine)
        super().tearDown()

    def project(self):
        project(self.engine, read_log())

    def differences(self):
        return differences(self.engine, read_log())

    def count(self, model):
        with self.engine.connect() as connection:
            return connection.scalar(select(func.count()).select_from(model))


class TestProjection(DatabaseCase):
    """Two entries, one of them scored, written into the test's working directory."""

    def setUp(self):
        super().setUp()
        write_entry(self.tmp)
        write_score(self.tmp)
        write_entry(self.tmp, "form-fixture-v1")

    def execute(self, statement):
        with self.engine.begin() as connection:
            connection.execute(statement)

    def test_the_projection_reconciles(self):
        self.project()
        self.assertEqual(self.differences(), [])
        self.assertEqual(self.count(Prediction), 6)
        self.assertEqual(self.count(PlayerScore), 3)

    def test_projecting_again_rebuilds_rather_than_adds(self):
        self.project()
        self.project()
        self.assertEqual(self.count(PredictionEntry), 2)
        self.assertEqual(self.count(Prediction), 6)
        self.assertEqual(self.differences(), [])

    def test_a_new_committed_entry_is_missing_until_projected(self):
        self.project()
        write_entry(self.tmp, "late-v1")
        found = self.differences()
        self.assertTrue(any("'late-v1'" in line and "not in the database" in line
                            for line in found))
        self.project()
        self.assertEqual(self.differences(), [])

    def test_an_edited_value_is_reported_with_both_sides(self):
        self.project()
        self.execute(update(Prediction).where(Prediction.player_id == 1)
                     .values(predicted_points=Prediction.predicted_points + Decimal("0.01")))
        found = self.differences()
        self.assertEqual(len(found), 2)
        self.assertTrue(all("predicted_points" in line and "in git" in line for line in found))

    def test_a_deleted_row_is_reported(self):
        self.project()
        self.execute(delete(PlayerScore).where(PlayerScore.player_id == 2))
        self.assertEqual(len(self.differences()), 1)

    def test_a_row_that_is_not_in_git_is_reported(self):
        self.project()
        with self.engine.begin() as connection:
            row = dict(connection.execute(select(Prediction.__table__).limit(1)).one()._mapping)
            connection.execute(insert(Prediction).values(row | {"player_id": 999}))
        self.assertTrue(any("999" in line and "not in git" in line
                            for line in self.differences()))

    def test_a_score_error_that_disagrees_with_its_columns_is_reported(self):
        path = self.tmp / "scores/gw08_baseline-v1.csv"
        rows = read_csv(path)
        rows[0]["error"] = "9.99"
        write_csv(path, rows, fieldnames=list(rows[0]))
        self.project()
        found = self.differences()
        self.assertEqual(len(found), 1)
        self.assertIn("error", found[0])

    def test_an_error_rounded_in_floats_reconciles(self):
        # 2.335 - 2 is 0.33499... as a float, so the harness wrote 0.33; the
        # exact Decimal 0.335 would round half to even, to 0.34.
        path = self.tmp / "predictions/gw08_baseline-v1.csv"
        rows = read_csv(path)
        rows[0]["predicted_points"] = "2.335"
        write_csv(path, rows)
        write_score(self.tmp, actual=(2, 2, 0))
        self.project()
        self.assertEqual(self.differences(), [])

    def test_every_digit_of_a_prediction_is_kept(self):
        path = self.tmp / "predictions/gw08_baseline-v1.csv"
        rows = read_csv(path)
        rows[0]["predicted_points"] = "5.125"
        write_csv(path, rows)
        write_score(self.tmp)
        self.project()
        with self.engine.connect() as connection:
            stored = connection.scalar(select(func.max(Prediction.predicted_points)))
        self.assertEqual(stored, Decimal("5.125"))
        self.assertEqual(self.differences(), [])


class TestTheCommittedLog(DatabaseCase):
    """The repository's own predictions/ and scores/, projected and reconciled."""

    def test_the_committed_log_reconciles(self):
        os.chdir(ROOT)
        self.project()
        self.assertEqual(self.differences(), [])
        self.assertEqual(self.count(PredictionEntry),
                         len(list((ROOT / "predictions").glob("*.csv"))))
