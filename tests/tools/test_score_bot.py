"""Tests for tools/score_bot.py and tools/readme_results.py, the score workflow (#162).

When to score, what may change, and what the README says are decided here on
canned bootstrap JSON, PR lists and score files, not left to the workflow
YAML: a scoring run against a mid-gameweek snapshot, or one that rewrote an
earlier gameweek's numbers, would put wrong figures on the front page.
"""

import importlib.util
import io
import json
import re
import sys
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from tests.support import fixtures

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


score_bot = _load("score_bot")
readme_results = _load("readme_results")

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
BRANCH = "score-bot/2026-27-gw10"


def event(gw, deadline, settled=True):
    return {"id": gw, "deadline_time": deadline, "finished": settled, "data_checked": settled}


def bootstrap(gw10_settled=True, gw11_started=False):
    return {
        "events": [
            event(9, "2026-10-31T11:00:00Z"),
            event(10, "2026-11-06T18:30:00Z", gw10_settled),
            event(11, "2026-11-11T11:00:00Z" if gw11_started else "2026-11-21T11:00:00Z", False),
        ]
    }


def pr(number, *paths, state="OPEN", branch="some-branch"):
    return {
        "number": number,
        "state": state,
        "headRefName": branch,
        "files": [{"path": p} for p in paths],
    }


def score_file(gw, model, actuals="20261110T235748Z", comparators=True, mae=1.0):
    return {
        "gameweek": gw,
        "model_version": model,
        "actuals_snapshot_id": actuals,
        "prediction_snapshot_id": "20261106T063726Z",
        "metrics": {"all": {"mae": mae, "spearman": 0.77777}},
        "comparators": (
            {"available": True, "form": {"mae": 1.1575, "xi_actual": 64}}
            if comparators
            else {"available": False}
        ),
        "xi": {"available": True, "captured": {"actual": 50}},
    }


class Repo(fixtures.TempCwd):
    """A season's log and scores in a temporary working directory."""

    def entry(self, gw, model, snapshot="20261106T063726Z"):
        fixtures.write_csv(
            Path(f"predictions/gw{gw:02d}_{model}.csv"),
            [{"gameweek": gw, "player_id": 1, "snapshot_id": snapshot}],
            fieldnames=["gameweek", "player_id", "snapshot_id"],
        )

    def scored(self, gw, model, **kwargs):
        path = Path(f"scores/gw{gw:02d}_{model}.json")
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(score_file(gw, model, **kwargs)))

    def decide(self, boot=None, prs=(), branches=("main",)):
        boot = boot or bootstrap()
        return score_bot.decide(boot, list(prs), set(branches), NOW)


class TestDecide(Repo):
    def setUp(self):
        super().setUp()
        for model in ("baseline-v1", "gbm-v1"):
            self.entry(9, model)
            self.scored(9, model)
            self.entry(10, model)

    def test_acts_once_a_logged_gameweek_settles(self):
        out = self.decide()
        self.assertEqual(out["action"], score_bot.ACT, out["why"])
        self.assertEqual(out["gameweeks"], [10])
        self.assertEqual(out["branch"], BRANCH)
        self.assertEqual((out["log_dir"], out["scores_dir"]), ("predictions", "scores"))

    def test_waits_while_results_are_not_final(self):
        out = self.decide(bootstrap(gw10_settled=False))
        self.assertEqual(out["action"], score_bot.SKIP)
        self.assertIn("GW10", out["why"])

    def test_waits_once_the_next_gameweek_has_started(self):
        """A snapshot then has zeroed rows for fixtures not yet played."""
        out = self.decide(bootstrap(gw11_started=True))
        self.assertEqual(out["action"], score_bot.SKIP)
        self.assertIn("GW11", out["why"])

    def test_skips_when_everything_settled_is_scored(self):
        for model in ("baseline-v1", "gbm-v1"):
            self.scored(10, model)
        self.assertEqual(self.decide()["action"], score_bot.SKIP)

    def test_an_entry_for_an_unsettled_gameweek_is_not_scored(self):
        for model in ("baseline-v1", "gbm-v1"):
            self.scored(10, model)
        self.entry(11, "gbm-v1")
        self.assertEqual(self.decide()["action"], score_bot.SKIP)

    def test_one_models_missing_score_is_enough(self):
        self.scored(10, "baseline-v1")
        out = self.decide()
        self.assertEqual((out["action"], out["gameweeks"]), (score_bot.ACT, [10]))

    def test_skips_while_its_own_pr_is_open(self):
        out = self.decide(prs=[pr(7, "scores/gw10_gbm-v1.json", branch=BRANCH)], branches=[BRANCH])
        self.assertEqual(out["action"], score_bot.SKIP)
        self.assertIn("#7", out["why"])

    def test_a_hand_made_score_pr_goes_first(self):
        out = self.decide(prs=[pr(8, "scores/cumulative.json", "README.md")])
        self.assertEqual(out["action"], score_bot.SKIP)
        self.assertIn("#8", out["why"])

    def test_closed_prs_do_not_block(self):
        out = self.decide(prs=[pr(8, "scores/cumulative.json", state="CLOSED")])
        self.assertEqual(out["action"], score_bot.ACT)

    def test_its_branch_without_an_open_pr_is_for_a_person(self):
        """A closed PR was a veto; a pushed branch with none is a half-finished run."""
        out = self.decide(prs=[pr(7, branch=BRANCH, state="CLOSED")], branches=["main", BRANCH])
        self.assertEqual(out["action"], score_bot.FAIL)
        self.assertIn("by hand", out["why"])

    def test_a_truncated_pr_list_fails(self):
        prs = [pr(n, state="MERGED") for n in range(score_bot.PR_LIMIT)]
        self.assertEqual(self.decide(prs=prs)["action"], score_bot.FAIL)

    def test_before_the_season_nothing_has_started(self):
        boot = {"events": [event(1, "2026-12-01T11:00:00Z", False)]}
        self.assertEqual(self.decide(boot)["action"], score_bot.SKIP)


class TestEntrySnapshots(Repo):
    def test_one_line_per_snapshot_of_the_new_gameweeks(self):
        self.entry(9, "gbm-v1", snapshot="20261031T004635Z")
        self.entry(10, "baseline-v1")
        self.entry(10, "gbm-v1")
        found = score_bot.entry_snapshots("predictions", {10})
        self.assertEqual(list(found), ["20261106T063726Z"])
        self.assertTrue(found["20261106T063726Z"].endswith("gw10_baseline-v1.csv"))


class TestSnapshotProblem(unittest.TestCase):
    def test_final_results_between_gameweeks(self):
        self.assertIsNone(score_bot.snapshot_problem(bootstrap(), [10], NOW))

    def test_unsettled_gameweek(self):
        self.assertIn("GW10", score_bot.snapshot_problem(bootstrap(False), [10], NOW))

    def test_mid_gameweek(self):
        problem = score_bot.snapshot_problem(bootstrap(gw11_started=True), [10], NOW)
        self.assertIn("between gameweeks", problem)


class TestChangeProblems(Repo):
    def setUp(self):
        super().setUp()
        self.before = {}
        self.scored(9, "gbm-v1", actuals="20261106T063726Z")
        self.before["scores/gw09_gbm-v1.json"] = Path("scores/gw09_gbm-v1.json").read_text()
        self.scored(9, "gbm-v1")  # rescored: only the actuals id moves

    def problems(self, *lines):
        return score_bot.change_problems(lines, {10}, "scores", self.before.__getitem__)

    def test_a_scoring_run(self):
        self.assertEqual(
            self.problems(
                "M\tREADME.md",
                "M\tscores/cumulative.json",
                "M\tscores/gw09_gbm-v1.json",
                "A\tscores/gw10_gbm-v1.csv",
                "A\tscores/gw10_gbm-v1.json",
            ),
            [],
        )

    def test_an_earlier_gameweeks_numbers_moving_is_refused(self):
        self.scored(9, "gbm-v1", mae=0.5)
        self.assertEqual(len(self.problems("M\tscores/gw09_gbm-v1.json")), 1)

    def test_an_earlier_gameweeks_csv_changing_is_refused(self):
        self.assertEqual(len(self.problems("M\tscores/gw09_gbm-v1.csv")), 1)

    def test_anything_outside_scores_and_the_readme_is_refused(self):
        for line in ("M\tpredictions/gw10_gbm-v1.csv", "A\tdocs/x.md", "D\tREADME.md"):
            with self.subTest(line=line):
                self.assertEqual(len(self.problems(line)), 1)

    def test_a_score_file_for_another_gameweek_is_refused(self):
        self.assertEqual(len(self.problems("A\tscores/gw11_gbm-v1.json")), 1)


class TestComparators(Repo):
    def test_a_new_score_file_without_comparators_is_refused(self):
        self.scored(10, "baseline-v1")
        self.scored(10, "gbm-v1", comparators=False)
        problems = score_bot.comparator_problems("scores", {10})
        self.assertEqual(len(problems), 1)
        self.assertIn("gbm-v1", problems[0])


class TestMessages(Repo):
    def test_commit_message_names_the_release(self):
        message = score_bot.commit_message([10], "20261110T235748Z", "snapshot-x", "a" * 64)
        self.assertTrue(message.startswith("Score GW10,"))
        self.assertIn("Actuals-Release: snapshot-x", message)

    def test_pr_body_has_each_new_result(self):
        self.scored(10, "gbm-v1")
        scores = score_bot.new_scores("scores", {10})
        body = score_bot.pr_body([10], "id", "tag", "sha", "rel", "run", scores)
        self.assertIn("| GW10 | `gbm-v1` | 1.000 | 0.778 | 1.158 | 50 | 64 |", body)

    def test_several_gameweeks_are_named_together(self):
        self.assertEqual(score_bot.named([9, 10, 11]), "GW9, GW10 and GW11")


README = """# x

### The record so far

From scores (actuals from snapshot `20261106T063726Z`, taken once settled).

| Model | Role | Scored | Missed | MAE | `form` MAE | XI points | `form` XI points |
|---|---|---|---|---|---|---|---|
| `gbm-v1` | challenger | GW9 | | 0.951 | 1.075 | 69 | 62 |
| `new-v1` | challenger | from GW12 | | | | | |

<details>
<summary><b>Results so far</b>, gameweek by gameweek</summary>

| Gameweek | Model | MAE | Spearman | `form` MAE | XI points | `form` XI points |
|---|---|---|---|---|---|---|
| GW9 | `gbm-v1` | 0.951 | 0.774 | 1.075 | 69 | 62 |

Text after.
"""


class TestReadmeResults(Repo):
    def setUp(self):
        super().setUp()
        self.scored(10, "gbm-v1")
        cumulative = {
            "gbm-v1": {
                "role": "challenger",
                "gameweeks_scored": [10],
                "gameweeks_missed": [],
                "pooled": {"all": {"mae": 1.0005}},
                "comparators": {"form": {"mae": 1.1575, "xi_actual": 64}},
                "xi": {"captured": 50},
            }
        }
        Path("scores/cumulative.json").write_text(json.dumps({"models": cumulative}))

    def test_tables_and_snapshot_follow_scores(self):
        out = readme_results.render(README, "scores")
        self.assertIn("(actuals from snapshot `20261110T235748Z`", out)
        self.assertIn("| `gbm-v1` | challenger | GW10 | | 1.001 | 1.158 | 50 | 64 |", out)
        self.assertIn("| GW10 | `gbm-v1` | 1.000 | 0.778 | 1.158 | 50 | 64 |", out)
        self.assertNotIn("| GW9 |", out)
        self.assertTrue(out.endswith("Text after.\n"))

    def test_a_model_with_no_scores_keeps_its_row(self):
        out = readme_results.render(README, "scores")
        self.assertIn("| `new-v1` | challenger | from GW12 | | | | | |", out)

    def test_rendering_twice_changes_nothing(self):
        once = readme_results.render(README, "scores")
        self.assertEqual(readme_results.render(once, "scores"), once)

    def test_mixed_actuals_snapshots_are_refused(self):
        self.scored(9, "gbm-v1", actuals="20261106T063726Z")
        with self.assertRaises(SystemExit):
            readme_results.render(README, "scores")


class TestTheRealReadme(unittest.TestCase):
    def test_it_matches_scores(self):
        root = TOOLS.parent
        text = (root / "README.md").read_text()
        self.assertEqual(readme_results.render(text, root / "scores"), text)


class TestWorkflow(unittest.TestCase):
    WORKFLOW = (TOOLS.parent / ".github" / "workflows" / "score.yml").read_text()

    def test_every_subcommand_it_calls_exists(self):
        for tool, module in (("score_bot", score_bot), ("entry_bot", sys.modules["entry_bot"])):
            called = set(re.findall(rf"tools/{tool}\.py ([a-z-]+)", self.WORKFLOW))
            self.assertTrue(called)
            with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                for command in called:
                    with self.subTest(command=command), self.assertRaises(SystemExit) as raised:
                        module.cli([command, "--help"])
                    self.assertEqual(raised.exception.code, 0)

    def test_checkout_does_not_use_the_bot_token(self):
        checkout = self.WORKFLOW.split("actions/checkout", 1)[1].split("- uses:", 1)[0]
        self.assertNotIn("ENTRY_BOT_TOKEN", checkout)

    def test_dry_runs_never_share_a_concurrency_group_with_real_runs(self):
        self.assertIn("github.run_id", self.WORKFLOW.split("concurrency:", 1)[1][:400])


if __name__ == "__main__":
    unittest.main()
