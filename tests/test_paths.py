"""Tests for fpl/paths.py: the commands run from the repository root or not at all.

The failure being prevented is silent. From the wrong directory fpl-fetch
would start a second data/raw/ with its own LATEST, and a predictor would write
a predictions/ that is not the log. So the refusal is tested through a real
command as well as directly, and "refused" includes "wrote nothing".

Run from the repository root: python -m unittest discover tests
"""

import contextlib
import io
import os
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

from fpl import predict_baseline
from fpl.paths import PROJECT_NAME, require_project_root

ROOT = Path(__file__).resolve().parent.parent


class InDirectory(unittest.TestCase):
    def setUp(self):
        self.prev = Path.cwd()
        self.tmp = Path(tempfile.mkdtemp())
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.prev)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pyproject(self, text):
        (self.tmp / "pyproject.toml").write_text(text)


class TestRequireProjectRoot(InDirectory):

    def test_refused_without_a_pyproject(self):
        with self.assertRaises(SystemExit) as raised:
            require_project_root()
        self.assertIn("repository root", str(raised.exception.code))

    def test_refused_for_another_projects_pyproject(self):
        self.pyproject('[project]\nname = "something-else"\n')
        with self.assertRaises(SystemExit):
            require_project_root()

    def test_refused_for_an_unreadable_pyproject(self):
        self.pyproject("[project\nname = ")
        with self.assertRaises(SystemExit):
            require_project_root()

    def test_passes_at_the_root(self):
        self.pyproject(f'[project]\nname = "{PROJECT_NAME}"\n')
        require_project_root()

    def test_a_command_refuses_and_writes_nothing(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
            predict_baseline.cli(["--gw", "6"])
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_help_still_works_anywhere(self):
        """--help is answered before the check: reading usage is never harmful."""
        with self.assertRaises(SystemExit) as raised, \
                contextlib.redirect_stdout(io.StringIO()):
            predict_baseline.cli(["--help"])
        self.assertEqual(raised.exception.code, 0)


class TestName(unittest.TestCase):

    def test_matches_pyproject(self):
        """Renaming the project without updating fpl/paths.py would lock every command."""
        with (ROOT / "pyproject.toml").open("rb") as f:
            self.assertEqual(tomllib.load(f)["project"]["name"], PROJECT_NAME)


if __name__ == "__main__":
    unittest.main()
