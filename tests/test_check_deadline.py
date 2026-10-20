"""Tests for tools/check_deadline.py, the deadline watchdog (#46).

The threshold and the filename match are tested here on canned bootstrap JSON,
not left to the workflow YAML: a watchdog that never fires is indistinguishable
from one that has nothing to say until the deadline it missed.

Run from the repository root: python -m unittest discover tests
"""

import importlib.util
import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

TOOL = Path(__file__).resolve().parent.parent / "tools" / "check_deadline.py"
_spec = importlib.util.spec_from_file_location("check_deadline", TOOL)
check_deadline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_deadline)

NOW = datetime(2026, 10, 7, 7, 0, tzinfo=UTC)


def bootstrap(gameweek, hours_away):
    """GW `gameweek` is next, `hours_away` from NOW; the one before it is past."""
    stamp = (NOW + timedelta(hours=hours_away)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"events": [
        {"id": gameweek - 1, "deadline_time": "2026-10-03T10:00:00Z", "is_next": False},
        {"id": gameweek, "deadline_time": stamp, "is_next": True},
    ]}


class TestCheck(unittest.TestCase):

    def test_fails_inside_the_window_with_no_entry(self):
        status, message = check_deadline.check(
            bootstrap(6, 48), ["gw04_baseline-v1.csv"], NOW)
        self.assertEqual(status, check_deadline.FAIL)
        self.assertIn("GW06", message)
        self.assertIn("gw06_*.csv", message)

    def test_passes_inside_the_window_with_an_entry(self):
        status, message = check_deadline.check(
            bootstrap(6, 48), ["gw04_baseline-v1.csv", "gw06_form-fixture-v1.csv"], NOW)
        self.assertEqual(status, check_deadline.PASS)
        self.assertIn("gw06_form-fixture-v1.csv", message)

    def test_passes_outside_the_window_and_says_so(self):
        status, message = check_deadline.check(bootstrap(6, 73), [], NOW)
        self.assertEqual(status, check_deadline.PASS)
        self.assertIn("No entry yet", message)

    def test_the_window_edge(self):
        """72 hours away is inside: the window is 'under or at', never later."""
        self.assertEqual(check_deadline.check(bootstrap(6, 72), [], NOW)[0],
                         check_deadline.FAIL)

    def test_a_deadline_already_passed_still_fails(self):
        """FPL can lag in moving is_next on; an entry-less past deadline is not fine."""
        self.assertEqual(check_deadline.check(bootstrap(6, -1), [], NOW)[0],
                         check_deadline.FAIL)

    def test_another_gameweeks_entry_does_not_count(self):
        names = ["gw05_baseline-v1.csv", "gw16_baseline-v1.csv", "gw60_baseline-v1.csv"]
        self.assertEqual(check_deadline.check(bootstrap(6, 24), names, NOW)[0],
                         check_deadline.FAIL)

    def test_the_log_filename_rule_decides_what_is_an_entry(self):
        """A bare gameweek or a stray file is not an entry, as fpl/log/contract.py reads names."""
        names = ["gw6_baseline-v1.csv", "gw06_baseline_v1.csv", "gw06.md", ".gitkeep"]
        self.assertEqual(check_deadline.check(bootstrap(6, 24), names, NOW)[0],
                         check_deadline.FAIL)

    def test_no_next_gameweek_passes(self):
        """After the last deadline of a season there is nothing to warn about."""
        season_over = {"events": [{"id": 38, "deadline_time": "2027-05-23T13:30:00Z",
                                   "is_next": False}]}
        status, message = check_deadline.check(season_over, [], NOW)
        self.assertEqual(status, check_deadline.PASS)
        self.assertIn("No next gameweek", message)


class TestMain(unittest.TestCase):
    """The command as the workflow runs it: exit codes, the API, the summary."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.predictions = self.dir / "predictions"
        self.predictions.mkdir()
        self.summary = self.dir / "summary.md"
        patcher = mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(self.summary)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_main(self, **kwargs):
        out = io.StringIO()
        with redirect_stdout(out):
            code = check_deadline.main(self.predictions, now=NOW, **kwargs)
        return code, out.getvalue()

    def test_a_fake_48h_deadline_with_no_entry_fails(self):
        code, out = self.run_main(fake_hours=48, fake_gameweek=6)
        self.assertEqual(code, 1)
        self.assertIn("FAKE", out)
        self.assertIn("DEADLINE AT RISK", self.summary.read_text())

    def test_the_same_with_an_entry_passes(self):
        (self.predictions / "gw06_baseline-v1.csv").write_text("gameweek\n6\n")
        code, _ = self.run_main(fake_hours=48, fake_gameweek=6)
        self.assertEqual(code, 0)
        self.assertIn("OK", self.summary.read_text())

    def test_reads_the_api_when_not_faked(self):
        with mock.patch.object(check_deadline, "get", return_value=bootstrap(6, 24)) as get:
            code, _ = self.run_main()
        get.assert_called_once_with("bootstrap-static/")
        self.assertEqual(code, 1)

    def test_an_unreachable_api_passes_and_says_could_not_check(self):
        with mock.patch.object(check_deadline, "get", side_effect=OSError("timed out")):
            code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("Could not check", out)
        self.assertIn("Could not check", self.summary.read_text())

    def test_fake_flags_go_together(self):
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                check_deadline.cli(["--fake-deadline-hours", "48"])


if __name__ == "__main__":
    unittest.main()
