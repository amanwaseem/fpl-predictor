"""Tests for fpl/db/url.py: refusing a DATABASE_URL that cannot work, without printing it.

No database needed. Two levels are checked: what cannot connect anywhere,
which libpq's own defaults do not rescue, and what the hosted database also
needs. Every malformed URL is built from sentinel values, and every message
is checked to contain none of them: any part of a URL may be its password.
"""

import contextlib
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from fpl.db import migrate, session
from fpl.db.session import database_url, exits_on_bad_url, make_engine, normalise_url
from fpl.db.url import DatabaseURLError, pooled, url_problems

ROOT = Path(__file__).resolve().parent.parent

USER, PASSWORD, HOST, DATABASE = "sentuserx", "SENTpw8Q", "senthost7.example", "sentdbz"
SENTINELS = (USER, PASSWORD, HOST, DATABASE, "npg_")
GOOD = f"postgresql://{USER}:{PASSWORD}@{HOST}/{DATABASE}?sslmode=require"
DEPLOY_DAY = f"postgresql://npg_{PASSWORD}"

# URLs libpq connects with, using its environment, ~/.pgpass, a socket or its
# defaults for whatever is left out. None may be refused outside hosting.
LIBPQ_ACCEPTS = {
    "no password (trust or peer auth, ~/.pgpass, PGPASSWORD)": "postgresql://fpl@localhost/fpl",
    "only a database": "postgresql:///fpl",
    "a Unix socket in the query": "postgresql:///db?host=/var/run/postgresql&user=u&password=p",
    "several hosts": "postgresql://u:p@h1:5432,h2:5433/db",
    "an IPv6 host": "postgresql://u:p@[::1]:5432/db",
    "spaces in the options": "postgresql://u:p@h/db?options=-c search_path=x",
    "another driver": "postgresql+psycopg2://u:p@h/db",
    "an uppercase scheme": "POSTGRESQL://u:p@h/db",
    "Docker's bare service host": "postgresql+psycopg://fpl:fpl@db:5432/fpl",
}

# Refused anywhere: each URL, and phrases its problems must include.
CANNOT_CONNECT = {
    "a password with no scheme": (f"npg_{PASSWORD}", ["no scheme"]),
    "not Postgres": (f"mysql://{USER}:{PASSWORD}@{HOST}/{DATABASE}", ["not postgresql"]),
    "quoted": (f"'{GOOD}'", ["quotes or spaces around it"]),
    "a trailing newline": (f"{GOOD}\n", ["quotes or spaces around it"]),
    "whitespace in the host": (GOOD.replace("@", " @"), ["whitespace outside the query"]),
    "a port that is not a number": (
        f"postgresql://{USER}:{PASSWORD}@{HOST}:port/{DATABASE}", ["port is not a number"]),
    "a port out of range": (
        f"postgresql://{USER}:{PASSWORD}@{HOST}:99999/{DATABASE}", ["between 1 and 65535"]),
    "an unclosed IPv6 address": (
        f"postgresql://{USER}:{PASSWORD}@[::1/{DATABASE}", ["IPv6"]),
    "one of several hosts with a bad port": (
        f"postgresql://{USER}:{PASSWORD}@{HOST}:5432,{HOST}:x/{DATABASE}",
        ["port is not a number"]),
}

# Fine for libpq, refused for the hosted database, where nothing fills them in.
INCOMPLETE = {
    "the deploy-day secret": (DEPLOY_DAY, ["no user", "being read as the host",
                                           "no password", "names no database"]),
    "no user": (f"postgresql://:{PASSWORD}@{HOST}/{DATABASE}", ["no user"]),
    "no password": (f"postgresql://{USER}@{HOST}/{DATABASE}", ["no password"]),
    "no host": (f"postgresql://{USER}:{PASSWORD}@/{DATABASE}", ["no host"]),
    "no database": (f"postgresql://{USER}:{PASSWORD}@{HOST}", ["names no database"]),
}


class TestWhatCannotConnect(unittest.TestCase):

    def test_urls_libpq_connects_with_pass(self):
        for name, url in LIBPQ_ACCEPTS.items():
            with self.subTest(name):
                self.assertEqual(url_problems(url), [])

    def test_each_url_that_cannot_connect_is_refused_naming_why(self):
        for name, (url, phrases) in CANNOT_CONNECT.items():
            with self.subTest(name):
                found = " ".join(url_problems(url))
                for phrase in phrases:
                    self.assertIn(phrase, found)

    def test_an_out_of_range_port_is_not_called_a_non_number(self):
        url, _ = CANNOT_CONNECT["a port out of range"]
        self.assertNotIn("not a number", " ".join(url_problems(url)))


class TestTheHostedDatabase(unittest.TestCase):

    def test_an_incomplete_url_passes_alone_but_not_for_hosting(self):
        for name, (url, phrases) in INCOMPLETE.items():
            with self.subTest(name):
                self.assertEqual(url_problems(url), [])
                found = " ".join(url_problems(url, complete=True))
                for phrase in phrases:
                    self.assertIn(phrase, found)

    def test_parts_may_come_from_the_query_string(self):
        url = f"postgresql:///?host={HOST}&user={USER}&password={PASSWORD}&dbname={DATABASE}"
        self.assertEqual(url_problems(url, complete=True), [])

    def test_fpl_hosted_switches_it_on(self):
        self.assertTrue(database_url({"DATABASE_URL": DEPLOY_DAY}))
        with self.assertRaises(DatabaseURLError):
            database_url({"DATABASE_URL": DEPLOY_DAY, "FPL_HOSTED": "1"})
        self.assertTrue(database_url({"DATABASE_URL": GOOD, "FPL_HOSTED": "1"}))

    def test_fly_sets_it(self):
        self.assertIn('FPL_HOSTED = "1"', (ROOT / "fly.toml").read_text())


class TestPooled(unittest.TestCase):
    """Neon's pooled endpoint: fine for the API, refused by migrations."""

    POOLED_URL = f"postgresql://{USER}:{PASSWORD}@ep-x-pooler.us-west-2.aws.neon.tech/db"

    def test_the_api_and_other_commands_accept_it(self):
        self.assertEqual(url_problems(self.POOLED_URL, complete=True), [])
        self.assertTrue(database_url({"DATABASE_URL": self.POOLED_URL, "FPL_HOSTED": "1"}))

    def test_migrations_refuse_it_without_printing_it(self):
        self.assertTrue(pooled(self.POOLED_URL))
        self.assertFalse(pooled(GOOD))
        with mock.patch.dict(os.environ, {"DATABASE_URL": self.POOLED_URL}), \
                self.assertRaises(SystemExit) as raised:
            migrate.cli([])
        self.assertIn("-pooler", str(raised.exception))
        self.assertNotIn(PASSWORD, str(raised.exception))


class TestTheDriverIsNamed(unittest.TestCase):

    def test_whatever_the_case_of_the_scheme(self):
        for url in ("postgresql://u:p@h/db", "POSTGRESQL://u:p@h/db", "Postgres://u:p@h/db"):
            with self.subTest(url=url):
                self.assertEqual(normalise_url(url), "postgresql+psycopg://u:p@h/db")

    def test_a_named_driver_is_kept_lowercased(self):
        self.assertEqual(normalise_url("PostgreSQL+Psycopg://u:p@h/db"),
                         "postgresql+psycopg://u:p@h/db")
        self.assertEqual(normalise_url("postgresql+asyncpg://u:p@h/db"),
                         "postgresql+asyncpg://u:p@h/db")


class TestNothingIsPrinted(unittest.TestCase):

    def assertNoSentinel(self, text):
        for sentinel in SENTINELS:
            self.assertNotIn(sentinel, text)

    def test_no_problem_quotes_the_url(self):
        for name, (url, _) in {**CANNOT_CONNECT, **INCOMPLETE}.items():
            with self.subTest(name):
                self.assertNoSentinel(" ".join(url_problems(url, complete=True)))

    def test_the_error_names_the_problem_and_not_the_url(self):
        for name, (url, phrases) in {**CANNOT_CONNECT, **INCOMPLETE}.items():
            with self.subTest(name), self.assertRaises(DatabaseURLError) as raised:
                database_url({"DATABASE_URL": url, "FPL_HOSTED": "1"})
            message = str(raised.exception)
            self.assertIn("not shown", message)
            for phrase in phrases:
                self.assertIn(phrase, message)
            self.assertNoSentinel(message)


class TestOneCheckAndOneExit(unittest.TestCase):

    def test_the_library_raises_an_ordinary_error_that_tests_can_catch(self):
        """Not SystemExit, which unittest's setUpClass does not catch."""
        self.assertTrue(issubclass(DatabaseURLError, ValueError))

    def test_a_command_turns_it_into_its_exit_with_the_message(self):
        @exits_on_bad_url
        def command():
            database_url({"DATABASE_URL": DEPLOY_DAY, "FPL_HOSTED": "1"})

        with self.assertRaises(SystemExit) as raised:
            command()
        self.assertIn("no user", str(raised.exception))

    def test_every_database_command_exits_cleanly_on_a_bad_url(self):
        from fpl.api import serve
        from fpl.db import load, project, reconcile
        commands = {"fpl-load": load.main, "fpl-project": project.main,
                    "fpl-reconcile": reconcile.main, "fpl-migrate": migrate.main,
                    "fpl-serve": lambda: serve.cli([])}
        for name, command in commands.items():
            with self.subTest(command=name), \
                    mock.patch.dict(os.environ, {"DATABASE_URL": f"npg_{PASSWORD}"}), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit) as raised:
                command()
            self.assertIn("no scheme", str(raised.exception))

    def test_a_url_handed_to_make_engine_is_not_checked_again(self):
        with mock.patch.object(session, "url_problems",
                               side_effect=AssertionError("checked twice")):
            make_engine("postgresql://u:p@h/db").dispose()


class TestTheDeployCheck(unittest.TestCase):
    """tools/check_database_url.py as the deploy runs it: no dependencies at all."""

    def run_python(self, *args, url=None):
        environ = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        if url is not None:
            environ["DATABASE_URL"] = url
        # -I ignores the environment and the script's directory; -S skips site,
        # so even this virtualenv's packages are out of reach, as on the
        # deploy runner, where nothing is installed.
        return subprocess.run([sys.executable, "-I", "-S", *args], capture_output=True,
                              text=True, env=environ, cwd="/")

    def check(self, url=None):
        return self.run_python(str(ROOT / "tools/check_database_url.py"), url=url)

    def test_the_isolation_is_real(self):
        """Proof the check below would catch a dependency creeping into fpl.db."""
        self.assertNotEqual(self.run_python("-c", "import sqlalchemy").returncode, 0)

    def test_a_complete_direct_url_passes(self):
        result = self.check(GOOD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(PASSWORD, result.stdout)

    def test_the_deploy_day_secret_fails_printing_none_of_it(self):
        result = self.check(DEPLOY_DAY)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("no user", result.stdout)
        for sentinel in SENTINELS:
            self.assertNotIn(sentinel, result.stdout + result.stderr)

    def test_a_pooled_url_fails_since_the_deploy_migrates(self):
        result = self.check(TestPooled.POOLED_URL)
        self.assertEqual(result.returncode, 1)
        self.assertIn("-pooler", result.stdout)

    def test_an_unset_url_fails(self):
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("not set", result.stdout)


if __name__ == "__main__":
    unittest.main()
