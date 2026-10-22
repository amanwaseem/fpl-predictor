"""Tests for fpl/db/url.py: refusing a DATABASE_URL that cannot work, without printing it.

No database needed. Every malformed URL here is built from sentinel values,
and every message is checked to contain none of them: any part of a URL may
be its password.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

from fpl.db.session import database_url, make_engine
from fpl.db.url import url_problems

ROOT = Path(__file__).resolve().parent.parent

USER, PASSWORD, HOST, DATABASE = "sentuserx", "SENTpw8Q", "senthost7.example", "sentdbz"
SENTINELS = (USER, PASSWORD, HOST, DATABASE, "npg_")
GOOD = f"postgresql://{USER}:{PASSWORD}@{HOST}/{DATABASE}?sslmode=require"

# Each malformed URL, and a phrase its problems must include.
MALFORMED = {
    "just a password after the scheme (the deploy-day secret)":
        (f"postgresql://npg_{PASSWORD}", ["no user", "no password", "names no database",
                                         "being read as the host"]),
    "a password with no scheme": (f"npg_{PASSWORD}", ["no scheme"]),
    "no user": (f"postgresql://:{PASSWORD}@{HOST}/{DATABASE}", ["no user"]),
    "no password": (f"postgresql://{USER}@{HOST}/{DATABASE}", ["no password"]),
    "no host": (f"postgresql://{USER}:{PASSWORD}@/{DATABASE}", ["no host"]),
    "no database": (f"postgresql://{USER}:{PASSWORD}@{HOST}", ["names no database"]),
    "an empty database": (f"postgresql://{USER}:{PASSWORD}@{HOST}/", ["names no database"]),
    "not Postgres": (f"mysql://{USER}:{PASSWORD}@{HOST}/{DATABASE}", ["not postgresql"]),
    "quoted": (f"'{GOOD}'", ["quotes or spaces around it"]),
    "a trailing newline": (f"{GOOD}\n", ["quotes or spaces around it"]),
    "whitespace inside": (GOOD.replace("@", " @"), ["contains whitespace"]),
    "a port that is not a number": (
        f"postgresql://{USER}:{PASSWORD}@{HOST}:port/{DATABASE}", ["port is a number"]),
    "Neon's pooled endpoint": (
        f"postgresql://{USER}:{PASSWORD}@ep-x-pooler.us-west-2.aws.neon.tech/{DATABASE}",
        ["pooled (-pooler) endpoint"]),
}


class TestShapes(unittest.TestCase):

    def test_working_urls_pass(self):
        for url in (
            GOOD,
            "postgres://fpl:fpl@localhost:5432/fpl",
            "postgresql+psycopg://fpl:fpl@db:5432/fpl",
            "postgresql://neondb_owner:abc@ep-billowing-1.c-4.us-west-2.aws.neon.tech"
            "/neondb?sslmode=require&channel_binding=require",
        ):
            with self.subTest(url=url):
                self.assertEqual(url_problems(url), [])

    def test_each_malformed_url_is_refused_naming_what_is_wrong(self):
        for name, (url, phrases) in MALFORMED.items():
            with self.subTest(name):
                found = " ".join(url_problems(url))
                for phrase in phrases:
                    self.assertIn(phrase, found)


class TestNothingIsPrinted(unittest.TestCase):
    """No part of the URL reaches a message, through any route."""

    def assertNoSentinel(self, text):
        for sentinel in SENTINELS:
            self.assertNotIn(sentinel, text)

    def test_the_problems_quote_none_of_the_url(self):
        for name, (url, _) in MALFORMED.items():
            with self.subTest(name):
                self.assertNoSentinel(" ".join(url_problems(url)))

    def test_commands_exit_naming_the_problem_and_not_the_url(self):
        for name, (url, phrases) in MALFORMED.items():
            with self.subTest(name), self.assertRaises(SystemExit) as raised:
                database_url({"DATABASE_URL": url})
            message = str(raised.exception)
            self.assertIn("cannot be used", message)
            for phrase in phrases:
                self.assertIn(phrase, message)
            self.assertIn("not shown", message)
            self.assertNoSentinel(message)

    def test_an_engine_is_never_built_from_a_malformed_url(self):
        url, _ = MALFORMED["just a password after the scheme (the deploy-day secret)"]
        with self.assertRaises(SystemExit) as raised:
            make_engine(url)
        self.assertNoSentinel(str(raised.exception))

    def test_a_working_url_still_gets_its_driver_named(self):
        self.assertTrue(database_url({"DATABASE_URL": GOOD}).startswith("postgresql+psycopg://"))


class TestTheDeployCheck(unittest.TestCase):
    """tools/check_database_url.py, as the deploy workflow runs it: plain python3,
    no dependencies installed, the URL only in the environment."""

    def run_check(self, url=None):
        environ = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        if url is not None:
            environ["DATABASE_URL"] = url
        # -I: isolated, so nothing from the virtualenv or site-packages helps it.
        return subprocess.run([sys.executable, "-I", str(ROOT / "tools/check_database_url.py")],
                              capture_output=True, text=True, env=environ, cwd="/")

    def test_a_working_url_passes(self):
        result = self.run_check(GOOD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(PASSWORD, result.stdout)

    def test_a_malformed_url_fails_and_prints_none_of_it(self):
        url, _ = MALFORMED["just a password after the scheme (the deploy-day secret)"]
        result = self.run_check(url)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no user", result.stdout)
        for sentinel in SENTINELS:
            self.assertNotIn(sentinel, result.stdout + result.stderr)

    def test_an_unset_url_fails(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("not set", result.stdout)


if __name__ == "__main__":
    unittest.main()
