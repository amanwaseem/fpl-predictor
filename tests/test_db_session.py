"""Tests for fpl.db.session: finding the database. No database needed."""

import unittest

from fpl.db.session import database_url, normalise_url


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
        with self.assertRaises(SystemExit) as raised:
            database_url({})
        self.assertIn("make up", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
