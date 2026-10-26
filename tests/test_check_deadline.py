"""Tests for tools/check_deadline.py, the deadline watchdog (#46, #68).

The threshold, the filename match and the season check are tested here on
canned bootstrap JSON, not left to the workflow YAML: a watchdog that never
fires is indistinguishable from one that has nothing to say until the
deadline it missed, and one that fires every week is learned as noise.

Run from the repository root: python -m unittest discover tests
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
_spec = importlib.util.spec_from_file_location("check_deadline", TOOLS / "check_deadline.py")
check_deadline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_deadline)

NOW = datetime(2026, 10, 7, 7, 0, tzinfo=UTC)
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def at(hours):
    return NOW + timedelta(hours=hours)


def bootstrap(gameweek, hours_away):
    """GW `gameweek` is next, `hours_away` from NOW; the one before it is past."""
    return {
        "events": [
            {"id": gameweek - 1, "deadline_time": "2026-10-03T10:00:00Z", "is_next": False},
            {"id": gameweek, "deadline_time": at(hours_away).strftime(STAMP), "is_next": True},
        ]
    }


def write_entry(directory, name, deadline):
    """An entry file as far as the watchdog reads one: a first row with deadline_utc."""
    stamp = deadline.strftime(STAMP) if isinstance(deadline, datetime) else deadline
    (Path(directory) / name).write_text("gameweek,player_id,deadline_utc\n6,1," + stamp + "\n")


def pr(number, *paths, state="OPEN", branch="b"):
    return {
        "number": number,
        "state": state,
        "headRefName": branch,
        "files": [{"path": p} for p in paths],
    }


class LogDir(unittest.TestCase):
    def setUp(self):
        self.log = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.log, ignore_errors=True)

    def check(self, hours, prs=(), gameweek=6):
        boot = bootstrap(gameweek, hours)
        return check_deadline.check(
            boot, lambda gw, d: check_deadline.classify_entries(gw, d, self.log), list(prs), NOW
        )


class TestAlarm(LogDir):
    def test_no_alarm_days_out_with_no_entry(self):
        """The entry workflow has not acted yet: that is not a risk."""
        status, message = self.check(48)
        self.assertEqual(status, check_deadline.PASS)
        self.assertIn("No entry on main yet", message)

    def test_no_alarm_just_outside_the_alarm(self):
        self.assertEqual(self.check(10.5)[0], check_deadline.PASS)

    def test_the_alarm_edge_is_inside(self):
        self.assertEqual(self.check(10)[0], check_deadline.FAIL)

    def test_inside_with_no_pr_says_make_it_by_hand(self):
        status, message = self.check(6)
        self.assertEqual(status, check_deadline.FAIL)
        self.assertIn("no open PR adds one", message)
        self.assertIn("by hand", message)

    def test_inside_with_an_open_pr_says_merge_it(self):
        status, message = self.check(6, [pr(80, "predictions/gw06_baseline-v1.csv")])
        self.assertEqual(status, check_deadline.FAIL)
        self.assertIn("merge #80", message)

    def test_an_open_pr_days_out_is_reported_without_an_alarm(self):
        status, message = self.check(48, [pr(80, "predictions/gw06_baseline-v1.csv")])
        self.assertEqual(status, check_deadline.PASS)
        self.assertIn("#80", message)

    def test_a_closed_pr_does_not_count(self):
        status, message = self.check(
            6, [pr(80, "predictions/gw06_baseline-v1.csv", state="CLOSED")]
        )
        self.assertEqual(status, check_deadline.FAIL)
        self.assertIn("no open PR", message)

    def test_a_pr_adding_files_outside_the_log_does_not_count(self):
        prs = [
            pr(
                81,
                "tests/fixtures/predictions/gw06_baseline-v1.csv",
                "scratch/predictions/gw06_baseline-v1.csv",
            )
        ]
        self.assertIn("no open PR", self.check(6, prs)[1])

    def test_a_deadline_already_passed_still_fails(self):
        """FPL can lag in moving is_next on; an entry-less past deadline is not fine."""
        self.assertEqual(self.check(-1)[0], check_deadline.FAIL)

    def test_passes_inside_the_alarm_with_an_entry(self):
        write_entry(self.log, "gw06_baseline-v1.csv", at(6))
        status, message = self.check(6)
        self.assertEqual(status, check_deadline.PASS)
        self.assertIn("gw06_baseline-v1.csv", message)


class TestWhatCountsAsAnEntry(LogDir):
    def test_another_gameweeks_entry_does_not_count(self):
        for name in ("gw05_baseline-v1.csv", "gw16_baseline-v1.csv", "gw60_baseline-v1.csv"):
            write_entry(self.log, name, at(6))
        self.assertEqual(self.check(6)[0], check_deadline.FAIL)

    def test_the_log_filename_rule_decides_what_is_an_entry(self):
        """A bare gameweek or a stray file is not an entry, as fpl/log/contract.py reads names."""
        for name in ("gw6_baseline-v1.csv", "gw06_baseline_v1.csv", "gw06.md", ".gitkeep"):
            write_entry(self.log, name, at(6))
        self.assertEqual(self.check(6)[0], check_deadline.FAIL)

    def test_last_seasons_entry_for_the_same_gameweek_does_not_count(self):
        """No season in the name: a year-old gw06 must not pass for this one."""
        write_entry(self.log, "gw06_baseline-v1.csv", at(6) - timedelta(days=365))
        status, message = self.check(6)
        self.assertEqual(status, check_deadline.FAIL)
        self.assertIn("another season", message)

    def test_a_deadline_fpl_moved_still_counts(self):
        write_entry(self.log, "gw06_baseline-v1.csv", at(6) - timedelta(days=2))
        status, message = self.check(6)
        self.assertEqual(status, check_deadline.PASS)
        self.assertIn("moved", message)

    def test_an_entry_with_no_readable_deadline_does_not_count(self):
        (self.log / "gw06_baseline-v1.csv").write_text("gameweek,player_id\n6,1\n")
        write_entry(self.log, "gw06_form-fixture-v1.csv", "not a time")
        (self.log / "gw06_other-v1.csv").write_text("")
        status, message = self.check(6)
        self.assertEqual(status, check_deadline.FAIL)
        self.assertIn("no readable deadline_utc", message)

    def test_a_missing_log_directory_is_no_entries(self):
        missing = self.log / "nope"
        found = check_deadline.classify_entries(6, at(6), missing)
        self.assertEqual(found, {"current": [], "moved": [], "other_season": [], "unreadable": []})


class TestBootstrap(LogDir):
    def test_no_next_gameweek_passes(self):
        """After the last deadline of a season there is nothing to warn about."""
        season_over = {
            "events": [{"id": 38, "deadline_time": "2027-05-23T13:30:00Z", "is_next": False}]
        }
        status, message = check_deadline.check(season_over, {}, [], NOW)
        self.assertEqual(status, check_deadline.PASS)
        self.assertIn("No next gameweek", message)

    def test_malformed_bootstraps_fail(self):
        """A schema change is not an outage: it will not fix itself next hour."""
        cases = {
            "no events": {},
            "events not a list": {"events": {"id": 6}},
            "two is_next": {
                "events": [
                    {"id": 6, "deadline_time": "2026-10-08T10:00:00Z", "is_next": True},
                    {"id": 7, "deadline_time": "2026-10-15T10:00:00Z", "is_next": True},
                ]
            },
            "no deadline": {"events": [{"id": 6, "is_next": True}]},
            "bad deadline": {"events": [{"id": 6, "deadline_time": "soon", "is_next": True}]},
            "bad id": {
                "events": [{"id": "6", "deadline_time": "2026-10-08T10:00:00Z", "is_next": True}]
            },
            "bool id": {
                "events": [{"id": True, "deadline_time": "2026-10-08T10:00:00Z", "is_next": True}]
            },
            "not a dict": [],
        }
        for name, boot in cases.items():
            with self.subTest(name):
                status, message = check_deadline.check(boot, {}, [], NOW)
                self.assertEqual(status, check_deadline.FAIL)
                self.assertIn("malformed", message)

    def test_season_of(self):
        self.assertEqual(check_deadline.season_of(datetime(2026, 8, 15, tzinfo=UTC)), "2026-27")
        self.assertEqual(check_deadline.season_of(datetime(2027, 5, 23, tzinfo=UTC)), "2026-27")
        self.assertEqual(check_deadline.season_of(datetime(2099, 9, 1, tzinfo=UTC)), "2099-00")

    def test_deadline_of_any_gameweek(self):
        boot = bootstrap(6, 6)
        self.assertEqual(
            check_deadline.deadline_of(boot, 5), datetime(2026, 10, 3, 10, 0, tzinfo=UTC)
        )
        self.assertIsNone(check_deadline.deadline_of(boot, 30))
        self.assertIsNone(
            check_deadline.deadline_of({"events": [{"id": 5, "deadline_time": None}]}, 5)
        )


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

    def test_a_fake_6h_deadline_with_no_entry_fails(self):
        code, out = self.run_main(fake_hours=6, fake_gameweek=6)
        self.assertEqual(code, 1)
        self.assertIn("FAKE", out)
        self.assertIn("DEADLINE AT RISK", self.summary.read_text())

    def test_the_same_with_an_entry_passes(self):
        write_entry(self.predictions, "gw06_baseline-v1.csv", at(6))
        code, _ = self.run_main(fake_hours=6, fake_gameweek=6)
        self.assertEqual(code, 0)
        self.assertIn("OK", self.summary.read_text())

    def test_a_fake_48h_deadline_passes(self):
        self.assertEqual(self.run_main(fake_hours=48, fake_gameweek=6)[0], 0)

    def test_the_pr_file_is_read(self):
        prs = self.dir / "prs.json"
        prs.write_text(json.dumps([pr(90, "predictions/gw06_baseline-v1.csv")]))
        code, out = self.run_main(prs_file=prs, fake_hours=6, fake_gameweek=6)
        self.assertEqual(code, 1)
        self.assertIn("merge #90", out)

    def test_a_pr_file_that_is_not_a_list_is_refused(self):
        prs = self.dir / "prs.json"
        prs.write_text("{}")
        with self.assertRaises(ValueError):
            self.run_main(prs_file=prs, fake_hours=6, fake_gameweek=6)

    def test_reads_the_api_when_not_faked(self):
        with mock.patch.object(check_deadline, "get", return_value=bootstrap(6, 6)) as get:
            code, _ = self.run_main()
        get.assert_called_once_with("bootstrap-static/")
        self.assertEqual(code, 1)

    def test_an_unreachable_api_passes_and_says_could_not_check(self):
        with mock.patch.object(check_deadline, "get", side_effect=OSError("timed out")):
            code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("Could not check", out)
        self.assertIn("Could not check", self.summary.read_text())

    def test_a_malformed_api_response_fails(self):
        with mock.patch.object(check_deadline, "get", return_value={"events": "x"}):
            code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("malformed", out)

    def test_fake_flags_go_together(self):
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                check_deadline.cli(["--fake-deadline-hours", "48"])


if __name__ == "__main__":
    unittest.main()
