"""Tests for fpl.db.session: finding the database. No database needed."""

import contextlib
import io
import os
import unittest
from pathlib import Path
from unittest import mock

from alembic import command
from alembic.config import Config

from fpl.db.session import database_url, normalise_url
from fpl.db.url import DatabaseURLError

ROOT = Path(__file__).resolve().parent.parent


class TestUrl(unittest.TestCase):

    def test_plain_postgres_urls_get_the_psycopg_driver(self):
        for url in ("postgres://u:p@host/db", "postgresql://u:p@host/db"):
            with self.subTest(url=url):
                self.assertEqual(normalise_url(url), "postgresql+psycopg://u:p@host/db")

    def test_a_url_naming_its_driver_is_left_alone(self):
        url = "postgresql+psycopg://u:p@host/db"
        self.assertEqual(normalise_url(url), url)

    def test_database_url_reads_the_environment(self):
        url = database_url({"DATABASE_URL": "postgres://u:p@host/db"})
        self.assertEqual(url, "postgresql+psycopg://u:p@host/db")

    def test_a_missing_url_says_how_to_get_one(self):
        with self.assertRaises(DatabaseURLError) as raised:
            database_url({})
        self.assertIn("make up", str(raised.exception))


class TestOfflineMigrations(unittest.TestCase):

    def test_the_sql_prints_without_a_database(self):
        """`alembic upgrade head --sql` is for reading the DDL; it needs no DATABASE_URL."""
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["configure_logger"] = False
        out = io.StringIO()
        config.stdout = out
        environ = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        with mock.patch.dict(os.environ, environ, clear=True), \
                contextlib.redirect_stdout(out):
            command.upgrade(config, "head", sql=True)
        self.assertIn("CREATE TABLE player_clubs", out.getvalue())


if __name__ == "__main__":
    unittest.main()
