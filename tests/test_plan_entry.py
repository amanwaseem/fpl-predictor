"""Tests for tools/plan_entry.py, the entry workflow's decision (#68).

The window, the duplicate checks and the outputs the workflow branches on are
tested here on canned bootstrap JSON and canned PR lists, not left to the
workflow YAML: a second entry for a gameweek cannot be undone (rule 1), and a
run that never acts looks the same as one with nothing to do.

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
# As when run as `python tools/plan_entry.py`: tools/ is on the path, so it
# can share the watchdog's reading of bootstrap and of entry names.
sys.path.insert(0, str(TOOLS))
_spec = importlib.util.spec_from_file_location("plan_entry", TOOLS / "plan_entry.py")
plan_entry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plan_entry)

NOW = datetime(2026, 10, 30, 23, 0, tzinfo=UTC)


def bootstrap(gameweek, hours_away):
    """GW `gameweek` is next, `hours_away` from NOW; the one before it is past."""
    stamp = (NOW + timedelta(hours=hours_away)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"events": [
        {"id": gameweek - 1, "deadline_time": "2026-10-23T17:30:00Z", "is_next": False},
        {"id": gameweek, "deadline_time": stamp, "is_next": True},
    ]}


def pr(number, *paths, branch="some-branch"):
    """An open PR as `gh pr list --json number,headRefName,files` lists it."""
    return {"number": number, "headRefName": branch,
            "files": [{"path": p, "additions": 1, "deletions": 0} for p in paths]}


def decide(gameweek=9, hours=6, names=(), prs=(), branches=(), **kwargs):
    return plan_entry.decide(bootstrap(gameweek, hours), list(names), list(prs),
                             set(branches), NOW, **kwargs)


class TestWindow(unittest.TestCase):

    def test_acts_inside_the_window(self):
        status, gameweek, deadline, why = decide(hours=6)
        self.assertEqual(status, plan_entry.ACT)
        self.assertEqual(gameweek, 9)
        self.assertEqual(deadline, "2026-10-31T05:00:00Z")
        self.assertIn("GW09", why)

    def test_the_window_edge_is_inside(self):
        """12 hours away acts: the window is 'at or under', never later."""
        self.assertEqual(decide(hours=12)[0], plan_entry.ACT)

    def test_skips_outside_the_window_and_says_a_later_run_will_act(self):
        status, _, _, why = decide(hours=12.5)
        self.assertEqual(status, plan_entry.SKIP)
        self.assertIn("later run", why)

    def test_skips_once_the_deadline_has_passed(self):
        """Rule 2: nothing is made after the deadline, even if is_next lags."""
        status, _, _, why = decide(hours=-0.5)
        self.assertEqual(status, plan_entry.SKIP)
        self.assertIn("stays missed", why)

    def test_ignore_window_acts_days_out_for_a_dry_run(self):
        status, _, _, why = decide(hours=96, ignore_window=True)
        self.assertEqual(status, plan_entry.ACT)
        self.assertIn("window ignored", why)

    def test_ignore_window_still_never_acts_after_the_deadline(self):
        self.assertEqual(decide(hours=-1, ignore_window=True)[0], plan_entry.SKIP)

    def test_no_next_gameweek_skips(self):
        season_over = {"events": [{"id": 38, "deadline_time": "2027-05-23T13:30:00Z",
                                   "is_next": False}]}
        status, gameweek, _, _ = plan_entry.decide(season_over, [], [], set(), NOW)
        self.assertEqual((status, gameweek), (plan_entry.SKIP, None))


class TestNoDuplicates(unittest.TestCase):

    def test_an_entry_on_main_skips(self):
        status, _, _, why = decide(names=["gw09_baseline-v1.csv"])
        self.assertEqual(status, plan_entry.SKIP)
        self.assertIn("gw09_baseline-v1.csv", why)

    def test_another_gameweeks_entry_on_main_does_not_count(self):
        self.assertEqual(decide(names=["gw08_baseline-v1.csv"])[0], plan_entry.ACT)

    def test_an_open_pr_from_this_workflow_skips(self):
        prs = [pr(80, "predictions/gw09_baseline-v1.csv", "reports/gw09_baseline-v1.md",
                  branch="predict/gw09")]
        status, _, _, why = decide(prs=prs, branches=["predict/gw09"])
        self.assertEqual(status, plan_entry.SKIP)
        self.assertIn("#80", why)

    def test_a_hand_made_pr_on_any_branch_skips(self):
        prs = [pr(81, "predictions/gw09_form-fixture-v1.csv", branch="my-gw9-entry")]
        status, _, _, why = decide(prs=prs)
        self.assertEqual(status, plan_entry.SKIP)
        self.assertIn("#81", why)

    def test_an_open_pr_for_another_gameweek_does_not_count(self):
        prs = [pr(82, "predictions/gw10_baseline-v1.csv")]
        self.assertEqual(decide(prs=prs)[0], plan_entry.ACT)

    def test_a_pr_that_only_touches_reports_or_scratch_does_not_count(self):
        """Only a file in predictions/ is an entry, as fpl/log.py names them."""
        prs = [pr(83, "reports/gw09_baseline-v1.md", "scratch/gw09_baseline-v1.csv")]
        self.assertEqual(decide(prs=prs)[0], plan_entry.ACT)

    def test_a_branch_with_no_pr_fails_for_a_person_to_look(self):
        status, _, _, why = decide(branches=["predict/gw09"])
        self.assertEqual(status, plan_entry.FAIL)
        self.assertIn("predict/gw09", why)

    def test_another_gameweeks_branch_does_not_count(self):
        """Merged entries' branches are kept; they must not block later weeks."""
        self.assertEqual(decide(branches=["predict/gw04", "predict/gw08"])[0],
                         plan_entry.ACT)

    def test_an_entry_on_main_wins_over_its_leftover_branch(self):
        status = decide(names=["gw09_baseline-v1.csv"], branches=["predict/gw09"])[0]
        self.assertEqual(status, plan_entry.SKIP)

    def test_duplicates_are_checked_even_outside_the_window(self):
        """A dry run ignoring the window still reports an existing entry."""
        status = decide(hours=96, names=["gw09_baseline-v1.csv"], ignore_window=True)[0]
        self.assertEqual(status, plan_entry.SKIP)


class TestMain(unittest.TestCase):
    """The command as the workflow runs it: exit codes, outputs, the summary."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.predictions = self.dir / "predictions"
        self.predictions.mkdir()
        self.prs = self.dir / "prs.json"
        self.prs.write_text("[]")
        self.branches = self.dir / "branches.txt"
        self.branches.write_text("main\npredict/gw08\n")
        self.summary = self.dir / "summary.md"
        self.outputs = self.dir / "outputs"
        patcher = mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(self.summary),
                                               "GITHUB_OUTPUT": str(self.outputs)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_main(self, **kwargs):
        out = io.StringIO()
        with redirect_stdout(out):
            code = plan_entry.main(self.predictions, self.prs, self.branches,
                                   now=NOW, **kwargs)
        return code, out.getvalue()

    def outputs_written(self):
        return dict(line.split("=", 1) for line in self.outputs.read_text().splitlines())

    def test_acting_writes_the_outputs_the_workflow_reads(self):
        code, _ = self.run_main(fake_hours=6, fake_gameweek=9)
        self.assertEqual(code, 0)
        self.assertEqual(self.outputs_written(),
                         {"act": "true", "gameweek": "9", "deadline": "2026-10-31T05:00:00Z"})
        self.assertIn("ACT", self.summary.read_text())

    def test_skipping_writes_act_false(self):
        code, _ = self.run_main(fake_hours=48, fake_gameweek=9)
        self.assertEqual(code, 0)
        self.assertEqual(self.outputs_written()["act"], "false")

    def test_an_open_pr_in_the_file_is_read(self):
        self.prs.write_text(json.dumps([pr(84, "predictions/gw09_baseline-v1.csv")]))
        self.run_main(fake_hours=6, fake_gameweek=9)
        self.assertEqual(self.outputs_written()["act"], "false")

    def test_a_leftover_branch_exits_non_zero(self):
        self.branches.write_text("main\npredict/gw09\n")
        code, _ = self.run_main(fake_hours=6, fake_gameweek=9)
        self.assertEqual(code, 1)
        self.assertEqual(self.outputs_written()["act"], "false")

    def test_reads_the_api_when_not_faked(self):
        with mock.patch.object(plan_entry, "get", return_value=bootstrap(9, 6)) as get:
            self.run_main()
        get.assert_called_once_with("bootstrap-static/")
        self.assertEqual(self.outputs_written()["act"], "true")

    def test_an_unreachable_api_skips_and_says_could_not_check(self):
        with mock.patch.object(plan_entry, "get", side_effect=OSError("timed out")):
            code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("Could not check", out)
        self.assertEqual(self.outputs_written()["act"], "false")

    def test_fake_flags_go_together(self):
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                plan_entry.cli(["--open-prs", str(self.prs),
                                "--remote-branches", str(self.branches),
                                "--fake-deadline-hours", "6"])


if __name__ == "__main__":
    unittest.main()
