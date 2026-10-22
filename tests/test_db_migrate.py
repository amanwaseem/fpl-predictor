"""Tests for fpl-migrate: the schema brought to head with no alembic.ini in reach.

Needs a database; see tests/database.py. Locally: make test-db
"""

import contextlib
import io
import os
from unittest import mock

from alembic import command
from sqlalchemy import inspect

from fpl.db import migrate
from fpl.db.models import Base
from fpl.db.session import make_engine
from tests.database import URL, alembic_config, require_test_database
from tests.fixtures import TempCwd


def setUpModule():
    require_test_database()


class TestMigrate(TempCwd):
    """Run from an empty directory, as in the deployed image: no checkout, no alembic.ini."""

    def setUp(self):
        super().setUp()
        command.downgrade(alembic_config(), "base")
        self.engine = make_engine(URL)

    def tearDown(self):
        self.engine.dispose()
        command.upgrade(alembic_config(), "head")
        super().tearDown()

    def run_migrate(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"DATABASE_URL": URL}), contextlib.redirect_stdout(out):
            self.assertEqual(migrate.cli([]), 0)
        return out.getvalue()

    def test_an_empty_database_is_brought_to_head(self):
        self.assertFalse((self.tmp / "alembic.ini").exists())
        output = self.run_migrate()
        self.assertIn(f"from empty to {migrate.head()}", output)
        tables = set(inspect(self.engine).get_table_names())
        self.assertEqual(tables, set(Base.metadata.tables) | {"alembic_version"})

    def test_a_database_at_head_is_left_alone(self):
        self.run_migrate()
        self.assertIn(f"already at {migrate.head()}", self.run_migrate())
