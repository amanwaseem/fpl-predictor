"""Tests for tools/entry_bot.py, the entry workflow's decisions and checks (#68).

The window, the duplicate checks, resuming a half-finished run, the guards
before anything is pushed, and the outputs the workflow branches on are
tested here on canned bootstrap JSON, PR lists and git output, not left to
the workflow YAML: a second entry for a gameweek cannot be undone (rule 1),
and a run that never acts looks the same as one with nothing to do.

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

from tests import fixtures

TOOLS = Path(__file__).resolve().parent.parent / "tools"
# As when run as `python tools/entry_bot.py`: tools/ is on the path, so it
# shares the watchdog's reading of bootstrap, the log and PRs.
sys.path.insert(0, str(TOOLS))
_spec = importlib.util.spec_from_file_location("entry_bot", TOOLS / "entry_bot.py")
entry_bot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(entry_bot)
check_deadline = sys.modules["check_deadline"]

NOW = datetime(2026, 10, 30, 23, 0, tzinfo=UTC)
STAMP = "%Y-%m-%dT%H:%M:%SZ"
SEASON = "2026-27"
BOT = f"entry-bot/{SEASON}-gw09"
SHA = "a" * 64


def at(hours):
    return NOW + timedelta(hours=hours)


def bootstrap(gameweek, hours_away, earlier=None):
    """GW `gameweek` is next, `hours_away` from NOW. `earlier` maps past GWs to deadlines."""
    earlier = earlier or {gameweek - 1: datetime(2026, 10, 23, 17, 30, tzinfo=UTC)}
    events = [
        {"id": gw, "deadline_time": d.strftime(STAMP), "is_next": False}
        for gw, d in sorted(earlier.items())
    ]
    events.append(
        {"id": gameweek, "deadline_time": at(hours_away).strftime(STAMP), "is_next": True}
    )
    return {"events": events}


def pr(number, *paths, state="OPEN", branch="some-branch"):
    """A PR as `gh pr list --state all --json number,state,headRefName,files` lists it."""
    return {
        "number": number,
        "state": state,
        "headRefName": branch,
        "files": [{"path": p, "additions": 1, "deletions": 0} for p in paths],
    }


def write_entry(directory, name, deadline):
    (Path(directory) / name).write_text(
        "gameweek,player_id,deadline_utc\n9,1," + deadline.strftime(STAMP) + "\n"
    )


class LogDir(unittest.TestCase):
    def setUp(self):
        self.log = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.log, ignore_errors=True)

    def decide(self, hours=6, prs=(), branches=("main",), gameweek=9, boot=None, **kwargs):
        boot = boot or bootstrap(gameweek, hours)
        return entry_bot.decide(boot, self.log, list(prs), set(branches), NOW, **kwargs)


class TestWindow(LogDir):
    def test_acts_inside_the_window(self):
        out = self.decide(hours=6)
        self.assertEqual(out["action"], entry_bot.ACT)
        self.assertEqual(
            (out["gameweek"], out["deadline"], out["season"], out["branch"]),
            (9, "2026-10-31T05:00:00Z", SEASON, BOT),
        )
        self.assertIn("GW09", out["why"])

    def test_the_window_edge_is_inside(self):
        """12 hours away acts: the window is 'at or under', never later."""
        self.assertEqual(self.decide(hours=12)["action"], entry_bot.ACT)

    def test_skips_outside_the_window_and_says_a_later_run_will_act(self):
        out = self.decide(hours=12.5)
        self.assertEqual(out["action"], entry_bot.SKIP)
        self.assertIn("later run", out["why"])

    def test_skips_once_the_deadline_has_passed(self):
        """Rule 2: nothing is made after the deadline, even if is_next lags."""
        for hours in (0, -0.5, -48):
            with self.subTest(hours=hours):
                out = self.decide(hours=hours)
                self.assertEqual(out["action"], entry_bot.SKIP)
                self.assertIn("stays missed", out["why"])

    def test_ignore_window_acts_days_out_for_a_dry_run(self):
        out = self.decide(hours=96, ignore_window=True)
        self.assertEqual(out["action"], entry_bot.ACT)
        self.assertIn("window ignored", out["why"])

    def test_ignore_window_still_never_acts_after_the_deadline(self):
        self.assertEqual(self.decide(hours=-1, ignore_window=True)["action"], entry_bot.SKIP)

    def test_no_next_gameweek_skips(self):
        season_over = {
            "events": [{"id": 38, "deadline_time": "2027-05-23T13:30:00Z", "is_next": False}]
        }
        out = self.decide(boot=season_over)
        self.assertEqual((out["action"], out["gameweek"]), (entry_bot.SKIP, None))

    def test_a_malformed_bootstrap_fails(self):
        out = self.decide(boot={"events": [{"id": 9, "is_next": True}]})
        self.assertEqual(out["action"], entry_bot.FAIL)
        self.assertIn("malformed", out["why"])

    def test_the_season_is_the_deadlines(self):
        out = self.decide(
            boot={"events": [{"id": 30, "deadline_time": "2027-03-13T13:30:00Z", "is_next": True}]},
            ignore_window=True,
        )
        self.assertEqual((out["season"], out["branch"]), ("2026-27", "entry-bot/2026-27-gw30"))


class TestNoDuplicates(LogDir):
    def test_an_entry_on_main_skips(self):
        write_entry(self.log, "gw09_baseline-v1.csv", at(6))
        out = self.decide()
        self.assertEqual(out["action"], entry_bot.SKIP)
        self.assertIn("gw09_baseline-v1.csv", out["why"])

    def test_an_entry_for_a_deadline_fpl_moved_still_skips(self):
        write_entry(self.log, "gw09_baseline-v1.csv", at(6) - timedelta(days=1))
        self.assertEqual(self.decide()["action"], entry_bot.SKIP)

    def test_another_gameweeks_entry_on_main_does_not_count(self):
        write_entry(self.log, "gw08_baseline-v1.csv", at(-150))
        self.assertEqual(self.decide()["action"], entry_bot.ACT)

    def test_last_seasons_entry_for_this_gameweek_fails_for_a_person(self):
        """No season in entry names: acting would collide with last season's file."""
        write_entry(self.log, "gw09_baseline-v1.csv", at(6) - timedelta(days=364))
        out = self.decide()
        self.assertEqual(out["action"], entry_bot.FAIL)
        self.assertIn("another season", out["why"])

    def test_last_seasons_entry_does_not_fail_outside_the_window(self):
        write_entry(self.log, "gw09_baseline-v1.csv", at(6) - timedelta(days=364))
        self.assertEqual(self.decide(hours=48)["action"], entry_bot.SKIP)

    def test_an_unreadable_entry_for_this_gameweek_fails(self):
        (self.log / "gw09_baseline-v1.csv").write_text("")
        self.assertEqual(self.decide()["action"], entry_bot.FAIL)

    def test_an_open_pr_from_this_workflow_skips(self):
        prs = [
            pr(80, "predictions/gw09_baseline-v1.csv", "reports/gw09_baseline-v1.md", branch=BOT)
        ]
        out = self.decide(prs=prs, branches=["main", BOT])
        self.assertEqual(out["action"], entry_bot.SKIP)
        self.assertIn("#80", out["why"])

    def test_a_hand_made_pr_on_any_branch_skips(self):
        prs = [pr(81, "predictions/gw09_form-fixture-v1.csv", branch="my-gw9-entry")]
        out = self.decide(prs=prs)
        self.assertEqual(out["action"], entry_bot.SKIP)
        self.assertIn("#81", out["why"])

    def test_an_open_pr_for_another_gameweek_does_not_count(self):
        self.assertEqual(
            self.decide(prs=[pr(82, "predictions/gw10_baseline-v1.csv")])["action"], entry_bot.ACT
        )

    def test_closed_and_merged_prs_do_not_count_as_pending(self):
        prs = [
            pr(83, "predictions/gw09_baseline-v1.csv", state="CLOSED"),
            pr(84, "predictions/gw09_baseline-v1.csv", state="MERGED"),
        ]
        self.assertEqual(self.decide(prs=prs)["action"], entry_bot.ACT)

    def test_only_a_file_directly_in_predictions_is_an_entry(self):
        """tests/fixtures/predictions/ is not the log, and must not suppress the entry."""
        prs = [
            pr(
                85,
                "reports/gw09_baseline-v1.md",
                "scratch/gw09_baseline-v1.csv",
                "tests/fixtures/predictions/gw09_baseline-v1.csv",
                "scratch/predictions/gw09_baseline-v1.csv",
                "predictions/gw9_baseline-v1.csv",
                "predictions/gw09_base_line.csv",
            )
        ]
        self.assertEqual(self.decide(prs=prs)["action"], entry_bot.ACT)

    def test_duplicates_are_checked_even_outside_the_window(self):
        write_entry(self.log, "gw09_baseline-v1.csv", at(96))
        self.assertEqual(self.decide(hours=96, ignore_window=True)["action"], entry_bot.SKIP)


class TestBranches(LogDir):
    def test_own_branch_with_no_pr_resumes(self):
        out = self.decide(branches=["main", BOT])
        self.assertEqual(out["action"], entry_bot.RESUME)
        self.assertEqual(out["branch"], BOT)

    def test_own_branch_whose_pr_was_closed_is_not_reopened(self):
        prs = [pr(86, "predictions/gw09_baseline-v1.csv", state="CLOSED", branch=BOT)]
        out = self.decide(prs=prs, branches=["main", BOT])
        self.assertEqual(out["action"], entry_bot.SKIP)
        self.assertIn("closed without merging", out["why"])

    def test_own_branch_whose_pr_merged_but_main_has_no_entry_fails(self):
        prs = [pr(87, "predictions/gw09_baseline-v1.csv", state="MERGED", branch=BOT)]
        out = self.decide(prs=prs, branches=["main", BOT])
        self.assertEqual(out["action"], entry_bot.FAIL)
        self.assertIn("reverted", out["why"])

    def test_own_branch_with_an_open_pr_adding_no_entry_fails(self):
        prs = [pr(88, "README.md", branch=BOT)]
        self.assertEqual(self.decide(prs=prs, branches=["main", BOT])["action"], entry_bot.FAIL)

    def test_last_seasons_own_branch_does_not_count(self):
        """Branch names carry the season, so last season's gw09 cannot block this one."""
        self.assertEqual(
            self.decide(branches=["main", "entry-bot/2025-26-gw09"])["action"], entry_bot.ACT
        )

    def test_a_hand_branch_with_no_pr_fails_inside_the_window(self):
        out = self.decide(branches=["main", "predict/gw09"])
        self.assertEqual(out["action"], entry_bot.FAIL)
        self.assertIn("predict/gw09", out["why"])

    def test_a_hand_branch_with_no_pr_does_not_fail_days_out(self):
        """Pushed on a Tuesday before its PR: not worth an hourly email for four days."""
        self.assertEqual(
            self.decide(hours=96, branches=["main", "predict/gw09"])["action"], entry_bot.SKIP
        )

    def test_a_hand_branch_with_no_pr_does_not_fail_after_the_deadline(self):
        self.assertEqual(
            self.decide(hours=-3, branches=["main", "predict/gw09"])["action"], entry_bot.SKIP
        )

    def test_a_hand_branch_with_any_past_pr_is_history(self):
        """Branches are kept: last season's predict/gw09, merged, must not block this one."""
        for state in ("MERGED", "CLOSED"):
            with self.subTest(state=state):
                prs = [
                    pr(89, "predictions/gw09_baseline-v1.csv", state=state, branch="predict/gw09")
                ]
                self.assertEqual(
                    self.decide(prs=prs, branches=["main", "predict/gw09"])["action"], entry_bot.ACT
                )

    def test_other_gameweeks_branches_do_not_count(self):
        branches = ["main", "predict/gw04", "predict/gw08", f"entry-bot/{SEASON}-gw08"]
        self.assertEqual(self.decide(branches=branches)["action"], entry_bot.ACT)


class TestLatePrs(LogDir):
    def test_an_open_pr_for_a_passed_deadline_is_late(self):
        prs = [pr(90, "predictions/gw08_baseline-v1.csv")]
        self.assertEqual(self.decide(hours=48, prs=prs)["late"], [(90, 8)])

    def test_late_prs_are_listed_whatever_the_decision(self):
        prs = [pr(90, "predictions/gw08_baseline-v1.csv")]
        out = self.decide(hours=6, prs=prs)
        self.assertEqual((out["action"], out["late"]), (entry_bot.ACT, [(90, 8)]))

    def test_this_gameweeks_open_pr_is_not_late(self):
        prs = [pr(91, "predictions/gw09_baseline-v1.csv")]
        self.assertEqual(self.decide(prs=prs)["late"], [])

    def test_closed_merged_and_non_log_prs_are_not_late(self):
        prs = [
            pr(92, "predictions/gw08_baseline-v1.csv", state="MERGED"),
            pr(93, "predictions/gw08_baseline-v1.csv", state="CLOSED"),
            pr(94, "reports/gw08_baseline-v1.md"),
        ]
        self.assertEqual(self.decide(prs=prs)["late"], [])

    def test_a_gameweek_bootstrap_does_not_know_is_not_late(self):
        prs = [pr(95, "predictions/gw37_baseline-v1.csv")]
        self.assertEqual(self.decide(prs=prs)["late"], [])

    def test_a_pr_is_listed_once_however_many_late_gameweeks(self):
        boot = bootstrap(9, 6, earlier={7: at(-200), 8: at(-150)})
        prs = [pr(96, "predictions/gw07_baseline-v1.csv", "predictions/gw08_baseline-v1.csv")]
        self.assertEqual(self.decide(boot=boot, prs=prs)["late"], [(96, 7)])


class TestDecideCommand(unittest.TestCase):
    """`decide` as the workflow runs it: exit codes, outputs, the summary."""

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
        patcher = mock.patch.dict(
            os.environ,
            {"GITHUB_STEP_SUMMARY": str(self.summary), "GITHUB_OUTPUT": str(self.outputs)},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def invoke(self, *extra):
        argv = [
            "decide",
            "--predictions",
            str(self.predictions),
            "--prs",
            str(self.prs),
            "--branches",
            str(self.branches),
            *extra,
        ]
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(entry_bot, "datetime", FrozenTime):
            code = entry_bot.cli(argv)
        return code, out.getvalue()

    def outputs_written(self):
        return dict(line.split("=", 1) for line in self.outputs.read_text().splitlines())

    def test_acting_writes_the_outputs_the_workflow_reads(self):
        code, _ = self.invoke("--fake-deadline-hours", "6", "--fake-gameweek", "9")
        self.assertEqual(code, 0)
        self.assertEqual(
            self.outputs_written(),
            {
                "action": "act",
                "gameweek": "9",
                "deadline": "2026-10-31T05:00:00Z",
                "season": SEASON,
                "branch": BOT,
                "late": "",
            },
        )
        self.assertIn("ACT", self.summary.read_text())

    def test_skipping_writes_action_skip(self):
        code, _ = self.invoke("--fake-deadline-hours", "48", "--fake-gameweek", "9")
        self.assertEqual(code, 0)
        self.assertEqual(self.outputs_written()["action"], "skip")

    def test_an_open_pr_in_the_file_is_read(self):
        self.prs.write_text(json.dumps([pr(84, "predictions/gw09_baseline-v1.csv")]))
        self.invoke("--fake-deadline-hours", "6", "--fake-gameweek", "9")
        self.assertEqual(self.outputs_written()["action"], "skip")

    def test_a_failure_exits_non_zero(self):
        self.branches.write_text("main\npredict/gw09\n")
        code, _ = self.invoke("--fake-deadline-hours", "6", "--fake-gameweek", "9")
        self.assertEqual(code, 1)
        self.assertEqual(self.outputs_written()["action"], "fail")

    def test_a_resume_exits_zero_with_the_branch(self):
        self.branches.write_text(f"main\n{BOT}\n")
        code, _ = self.invoke("--fake-deadline-hours", "6", "--fake-gameweek", "9")
        self.assertEqual(code, 0)
        self.assertEqual(self.outputs_written()["action"], "resume")
        self.assertEqual(self.outputs_written()["branch"], BOT)

    def test_a_list_as_long_as_the_limit_fails(self):
        """A truncated list could hide the PR that already adds this entry."""
        self.prs.write_text(
            json.dumps([pr(n, "README.md", state="MERGED") for n in range(entry_bot.PR_LIMIT)])
        )
        code, out = self.invoke("--fake-deadline-hours", "6", "--fake-gameweek", "9")
        self.assertEqual(code, 1)
        self.assertIn("truncated", out)

    def test_an_empty_branch_list_is_refused(self):
        """No branches at all, not even main, means the listing failed."""
        self.branches.write_text("")
        with self.assertRaises(ValueError):
            self.invoke("--fake-deadline-hours", "6", "--fake-gameweek", "9")

    def test_late_prs_are_written_out(self):
        self.prs.write_text(json.dumps([pr(97, "predictions/gw08_baseline-v1.csv")]))
        with mock.patch.object(check_deadline, "get", return_value=bootstrap(9, 48)):
            code, out = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(self.outputs_written()["late"], "97")
        self.assertIn("LATE: #97", out)

    def test_an_unreachable_api_skips_and_says_could_not_check(self):
        with mock.patch.object(check_deadline, "get", side_effect=OSError("timed out")):
            code, out = self.invoke()
        self.assertEqual(code, 0)
        self.assertIn("Could not check", out)
        self.assertEqual(self.outputs_written()["action"], "skip")

    def test_fake_flags_go_together(self):
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                entry_bot.cli(
                    [
                        "decide",
                        "--prs",
                        str(self.prs),
                        "--branches",
                        str(self.branches),
                        "--fake-deadline-hours",
                        "6",
                    ]
                )


class FrozenTime(datetime):
    """datetime with now() fixed at NOW, for the commands that read the clock."""

    @classmethod
    def now(cls, tz=None):
        return NOW


class TestCheckSnapshot(fixtures.TempCwd):
    def setUp(self):
        super().setUp()
        self.id = "20261030T225500Z"
        self.snapshot(self.id, events=fixtures.season(9, at(6).strftime(STAMP)), latest=True)

    def problem(self, snapshot_id=None, gameweek=9, now=NOW, max_age=60):
        return entry_bot.snapshot_problem(snapshot_id or self.id, gameweek, now, max_age)

    def test_a_fresh_snapshot_for_the_planned_gameweek_passes(self):
        self.assertIsNone(self.problem())

    def test_a_snapshot_for_another_gameweek_is_refused(self):
        self.assertIn("predicts GW9, not the planned GW10", self.problem(gameweek=10))

    def test_an_old_snapshot_is_refused(self):
        """LATEST unmoved by a failed fetch would otherwise be used silently."""
        self.assertIn("minutes old", self.problem(now=NOW + timedelta(hours=3)))

    def test_a_snapshot_from_the_future_is_refused(self):
        self.assertIn("future", self.problem(now=NOW - timedelta(hours=1)))

    def test_a_bad_id_is_refused(self):
        for bad in ("", "LATEST", "../etc", "2026-10-30"):
            with self.subTest(bad=bad):
                self.assertIn("not a snapshot id", entry_bot.snapshot_problem(bad, 9, NOW, 60))

    def test_a_missing_snapshot_is_refused(self):
        self.assertIn("cannot be predicted from", self.problem("20261030T225900Z"))

    def test_the_command(self):
        with (
            redirect_stdout(io.StringIO()) as out,
            mock.patch.object(entry_bot, "datetime", FrozenTime),
        ):
            code = entry_bot.cli(["check-snapshot", "--snapshot", self.id, "--gameweek", "9"])
        self.assertEqual(code, 0, out.getvalue())


class TestCheckChanges(unittest.TestCase):
    def problems(self, *lines, gameweek=9):
        return entry_bot.unexpected_changes(lines, gameweek)

    def test_entries_with_their_digests_pass(self):
        self.assertEqual(
            self.problems(
                "A\tpredictions/gw09_baseline-v1.csv",
                "A\treports/gw09_baseline-v1.md",
                "A\tpredictions/gw09_form-fixture-v1.csv",
                "A\treports/gw09_form-fixture-v1.md",
                "",
            ),
            [],
        )

    def test_a_changed_committed_entry_is_refused(self):
        """Rule 1: an earlier entry modified alongside is never part of an entry PR."""
        found = self.problems(
            "A\tpredictions/gw09_baseline-v1.csv",
            "A\treports/gw09_baseline-v1.md",
            "M\tpredictions/gw08_baseline-v1.csv",
        )
        self.assertTrue(any("'M'" in p for p in found))

    def test_deletions_and_renames_are_refused(self):
        for line in (
            "D\tpredictions/gw08_baseline-v1.csv",
            "R100\tpredictions/gw08_a.csv\tpredictions/gw09_a.csv",
        ):
            with self.subTest(line=line):
                self.assertTrue(
                    self.problems(
                        "A\tpredictions/gw09_baseline-v1.csv",
                        "A\treports/gw09_baseline-v1.md",
                        line,
                    )
                )

    def test_another_gameweeks_entry_is_refused(self):
        self.assertTrue(
            self.problems(
                "A\tpredictions/gw09_baseline-v1.csv",
                "A\treports/gw09_baseline-v1.md",
                "A\tpredictions/gw10_baseline-v1.csv",
            )
        )

    def test_anything_outside_the_entry_is_refused(self):
        for path in (
            "scores/cumulative.json",
            "fpl/log/entry.py",
            ".github/workflows/ci.yml",
            "predictions/gw09_baseline-v1.csv.tmp",
            "predictions/sub/gw09_x.csv",
            "data/raw/LATEST",
        ):
            with self.subTest(path=path):
                self.assertTrue(
                    self.problems(
                        "A\tpredictions/gw09_baseline-v1.csv",
                        "A\treports/gw09_baseline-v1.md",
                        f"A\t{path}",
                    )
                )

    def test_no_entry_at_all_is_refused(self):
        self.assertIn("no predictions/gw09_*.csv among the changes", self.problems())

    def test_an_entry_without_its_digest_is_refused(self):
        found = self.problems(
            "A\tpredictions/gw09_baseline-v1.csv",
            "A\tpredictions/gw09_form-fixture-v1.csv",
            "A\treports/gw09_baseline-v1.md",
        )
        self.assertEqual(found, ["gw09_form-fixture-v1.csv has no reports/gw09_form-fixture-v1.md"])

    def test_a_digest_without_its_entry_is_refused(self):
        self.assertTrue(
            self.problems(
                "A\tpredictions/gw09_baseline-v1.csv",
                "A\treports/gw09_baseline-v1.md",
                "A\treports/gw09_other-v1.md",
            )
        )

    def test_the_command_reads_stdin(self):
        with (
            mock.patch("sys.stdin", io.StringIO("A\tscores/x.json\n")),
            redirect_stdout(io.StringIO()) as out,
        ):
            code = entry_bot.cli(["check-changes", "--gameweek", "9"])
        self.assertEqual(code, 1)
        self.assertIn("UNEXPECTED", out.getvalue())


class TestCommitMessageAndTrailers(unittest.TestCase):
    def message(self, models=("baseline-v1", "form-fixture-v1")):
        return entry_bot.commit_message(
            9,
            "2026-10-31T11:00:00Z",
            "20261030T230000Z",
            "snapshot-20261030T230000Z",
            SHA,
            list(models),
        )

    def test_the_subject_names_the_models(self):
        self.assertTrue(
            self.message().startswith(
                "Log GW9 predictions: baseline-v1 and form-fixture-v1, from one snapshot\n"
            )
        )
        self.assertIn("a, b and c,", self.message(["a", "b", "c"]))
        self.assertIn("predictions: a, from", self.message(["a"]))

    def test_trailers_round_trip(self):
        found = entry_bot.read_trailers(self.message())
        self.assertEqual(
            found,
            {
                "Entry-Made-By": "entry-automation",
                "Snapshot-Release": "snapshot-20261030T230000Z",
                "Snapshot-SHA256": SHA,
            },
        )

    def test_a_commit_not_made_by_the_workflow_is_refused(self):
        """A person's commit on the bot branch is not resumed as if it were the bot's."""
        with self.assertRaises(ValueError):
            entry_bot.read_trailers(self.message().replace("entry-automation", "me"))
        with self.assertRaises(ValueError):
            entry_bot.read_trailers("Log GW9 predictions\n")

    def test_missing_or_malformed_snapshot_trailers_are_refused(self):
        for old, new in (
            ("Snapshot-SHA256: " + SHA, ""),
            ("Snapshot-Release: snapshot-20261030T230000Z", ""),
            (SHA, "not-a-sha"),
            ("snapshot-20261030T230000Z\n", "snapshot-../x\n"),
        ):
            with self.subTest(old=old):
                with self.assertRaises(ValueError):
                    entry_bot.read_trailers(self.message().replace(old, new))

    def test_the_read_trailers_command_writes_outputs(self):
        outputs = Path(tempfile.mkdtemp()) / "out"
        self.addCleanup(shutil.rmtree, outputs.parent)
        with (
            mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(outputs)}),
            mock.patch("sys.stdin", io.StringIO(self.message())),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(entry_bot.cli(["read-trailers"]), 0)
        self.assertIn("snapshot=20261030T230000Z", outputs.read_text())
        with mock.patch("sys.stdin", io.StringIO("nothing")), redirect_stdout(io.StringIO()):
            self.assertEqual(entry_bot.cli(["read-trailers"]), 1)

    def test_plain_prints_only_the_tag_and_sha(self):
        with (
            mock.patch("sys.stdin", io.StringIO(self.message())),
            redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(entry_bot.cli(["read-trailers", "--plain"]), 0)
        self.assertEqual(out.getvalue(), f"snapshot-20261030T230000Z {SHA}\n")

    def test_plain_prints_nothing_to_stdout_when_refused(self):
        """The shell reads stdout: a refusal must leave it empty, not half-filled."""
        with (
            mock.patch("sys.stdin", io.StringIO("nothing")),
            redirect_stdout(io.StringIO()) as out,
            mock.patch("sys.stderr", io.StringIO()),
        ):
            self.assertEqual(entry_bot.cli(["read-trailers", "--plain"]), 1)
        self.assertEqual(out.getvalue(), "")


class TestPrBody(unittest.TestCase):
    def body(self, resumed=False):
        return entry_bot.pr_body(
            9,
            "2026-10-31T11:00:00Z",
            "20261030T230000Z",
            "snapshot-20261030T230000Z",
            SHA,
            "https://r",
            "https://run",
            "## GW9 entry\n\nincluded: 1, 2\n",
            resumed,
        )

    def test_carries_the_deadline_snapshot_checklist_and_digest(self):
        body = self.body()
        for text in (
            "Merge before `2026-10-31T11:00:00Z`",
            "`20261030T230000Z`",
            "[`snapshot-20261030T230000Z`](https://r)",
            SHA,
            "https://run",
            "- [ ] `ci` is green",
            "## GW9 entry",
            "included: 1, 2",
        ):
            self.assertIn(text, body)
        self.assertNotIn("later run", body)

    def test_a_resumed_pr_says_so(self):
        self.assertIn("opened by a later run", self.body(resumed=True))

    def test_has_no_em_dashes(self):
        self.assertNotIn("\u2014", self.body(True))


class TestBeforeDeadline(unittest.TestCase):
    def invoke(self, minutes_left, margin=2):
        deadline = (NOW + timedelta(minutes=minutes_left)).strftime(STAMP)
        with redirect_stdout(io.StringIO()), mock.patch.object(entry_bot, "datetime", FrozenTime):
            return entry_bot.cli(
                ["before-deadline", "--deadline", deadline, "--margin-minutes", str(margin)]
            )

    def test_well_before_passes(self):
        self.assertEqual(self.invoke(60), 0)

    def test_inside_the_margin_or_after_is_refused(self):
        for minutes in (2, 1, 0, -30):
            with self.subTest(minutes=minutes):
                self.assertEqual(self.invoke(minutes), 1)


class TestTokenExpiry(unittest.TestCase):
    def invoke(self, headers, fail_days=7):
        with (
            mock.patch("sys.stdin", io.StringIO(headers)),
            redirect_stdout(io.StringIO()) as out,
            mock.patch.object(entry_bot, "datetime", FrozenTime),
        ):
            code = entry_bot.cli(["token-expiry", "--fail-days", str(fail_days)])
        return code, out.getvalue()

    def response(self, expiry=None, status="HTTP/2.0 200 OK"):
        lines = [status, "Content-Type: application/json"]
        if expiry:
            lines.append(f"Github-Authentication-Token-Expiration: {expiry}")
        return "\r\n".join(lines) + "\r\n\r\n{}"

    def test_a_token_with_time_left_passes(self):
        code, out = self.invoke(self.response("2027-01-30 12:00:00 UTC"))
        self.assertEqual(code, 0)
        self.assertIn("expires in 92 days", out)

    def test_a_token_expiring_soon_fails(self):
        code, out = self.invoke(self.response("2026-11-03 12:00:00 UTC"))
        self.assertEqual(code, 1)
        self.assertIn("Replace it now", out)

    def test_an_expired_token_fails(self):
        self.assertEqual(self.invoke(self.response("2026-10-01 00:00:00 UTC"))[0], 1)

    def test_the_offset_form_is_read(self):
        self.assertEqual(self.invoke(self.response("2027-01-30 12:00:00 +0000"))[0], 0)

    def test_a_token_without_expiry_passes(self):
        code, out = self.invoke(self.response())
        self.assertEqual(code, 0)
        self.assertIn("does not expire", out)

    def test_a_rejected_token_fails(self):
        for status in ("HTTP/2.0 401 Unauthorized", "HTTP/2.0 403 Forbidden", ""):
            with self.subTest(status=status):
                code, out = self.invoke(self.response(status=status) if status else "")
                self.assertEqual(code, 1)
                self.assertIn("rejected", out)

    def test_an_unreadable_expiry_fails(self):
        self.assertEqual(self.invoke(self.response("next tuesday"))[0], 1)


class TestWorkflow(unittest.TestCase):
    """The workflow calls only what exists here, and keeps the token where it belongs.

    Read as text: there is no YAML parser in the standard library, and these are
    properties of the file, not of a parse of it.
    """

    WORKFLOW = (TOOLS.parent / ".github" / "workflows" / "entry.yml").read_text()

    def test_every_subcommand_it_calls_exists(self):
        import re

        called = set(re.findall(r"tools/entry_bot\.py ([a-z-]+)", self.WORKFLOW))
        self.assertTrue(called)
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            for command in called:
                with self.subTest(command=command), self.assertRaises(SystemExit) as raised:
                    entry_bot.cli([command, "--help"])
                self.assertEqual(raised.exception.code, 0)

    def test_checkout_does_not_use_the_bot_token(self):
        """An expired token must fail one step, not every hourly checkout."""
        checkout = self.WORKFLOW.split("actions/checkout", 1)[1].split("- uses:", 1)[0]
        self.assertNotIn("ENTRY_BOT_TOKEN", checkout)

    def test_dry_runs_never_share_a_concurrency_group_with_real_runs(self):
        self.assertIn("github.run_id", self.WORKFLOW.split("concurrency:", 1)[1][:400])


if __name__ == "__main__":
    unittest.main()
