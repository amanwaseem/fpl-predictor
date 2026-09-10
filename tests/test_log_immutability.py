"""Tests for the mechanical enforcement of hard rule 1.

`fpl/log.py` stops a second *write* through write_entry. It does nothing about
an editor, a `sed -i`, or a rebase — the ways a committed prediction actually
gets altered. `tools/check_log_immutable.py` is what catches those, so these
tests exercise it against a real git repository rather than a mock: the tool's
entire job is reading git, and a mocked git would test the mock.

Run from the repository root: python -m unittest discover tests
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "check_log_immutable.py"

ENTRY = "gameweek,player_id,predicted_points\n4,1,6.30\n"


class FixtureRepo(unittest.TestCase):
    """A throwaway git repo with one committed prediction entry.

    Global and system git config are pointed at os.devnull so that whatever
    the developer has set — a signing key, a default branch name, a hook
    template — cannot change what these tests exercise.
    """

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.write("predictions/gw04_baseline-v1.csv", ENTRY)
        self.write("scores/gw04_baseline-v1.json", '{"mae": 2.1}\n')
        self.git("add", "-A")
        self.git("commit", "-qm", "Commit the GW4 entry")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True,
                       stdout=subprocess.DEVNULL)

    def write(self, relpath, text):
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def check(self, ref="HEAD"):
        return subprocess.run(
            [sys.executable, str(TOOL), "--ref", ref],
            cwd=self.repo, env=self.env, capture_output=True, text=True,
        )


class TestImmutabilityChecker(FixtureRepo):
    def test_untouched_tree_passes(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unchanged", result.stdout)

    def test_modified_entry_fails_and_names_the_path(self):
        self.write("predictions/gw04_baseline-v1.csv",
                   "gameweek,player_id,predicted_points\n4,1,9.99\n")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("predictions/gw04_baseline-v1.csv", result.stderr)
        self.assertIn("modified", result.stderr)

    def test_deleted_entry_fails(self):
        (self.repo / "predictions/gw04_baseline-v1.csv").unlink()
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("deleted", result.stderr)

    def test_new_entry_passes(self):
        """Appending is the whole point. A new gameweek is not a violation."""
        self.write("predictions/gw05_baseline-v1.csv", ENTRY)
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gw05_baseline-v1.csv", result.stdout)

    def test_a_new_entry_alongside_a_modified_one_still_fails(self):
        """Adding a legitimate entry must not launder an edit to an old one."""
        self.write("predictions/gw05_baseline-v1.csv", ENTRY)
        self.write("predictions/gw04_baseline-v1.csv", "tampered\n")
        self.assertEqual(self.check().returncode, 1)

    def test_whitespace_only_change_still_fails(self):
        """Byte comparison, not a semantic diff — 'it only reordered' is
        exactly the argument rule 1 exists to refuse."""
        self.write("predictions/gw04_baseline-v1.csv", ENTRY + "\n")
        self.assertEqual(self.check().returncode, 1)

    def test_scores_are_not_covered(self):
        """scores/ is derived output, regenerable, and meant to be rewritten
        when a metric definition changes. Freezing it would freeze the metrics
        instead of the evidence."""
        self.write("scores/gw04_baseline-v1.json", '{"mae": 1.7}\n')
        self.assertEqual(self.check().returncode, 0)

    def test_unresolvable_ref_fails_closed(self):
        """An unverifiable log is not a verified one."""
        result = self.check(ref="origin/nonexistent")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot resolve", result.stderr)

    def test_reports_when_nothing_is_committed_yet(self):
        empty = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q"], cwd=empty, env=self.env, check=True)
        subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=empty,
                       env=self.env, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=empty,
                       env=self.env, check=True)
        (empty / "README.md").write_text("x\n")
        subprocess.run(["git", "add", "-A"], cwd=empty, env=self.env, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=empty, env=self.env,
                       check=True)
        result = subprocess.run(
            [sys.executable, str(TOOL), "--ref", "HEAD"],
            cwd=empty, env=self.env, capture_output=True, text=True,
        )
        shutil.rmtree(empty, ignore_errors=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing to protect yet", result.stdout)
