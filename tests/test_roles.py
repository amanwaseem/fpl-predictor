"""The models' roles, fpl.log.roles (#109, ADR 0020)."""

import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from fpl.evaluation.score import missed_gameweeks
from fpl.log import entry, roles
from tests import fixtures

ROOT = Path(__file__).resolve().parent.parent
SEASON = "2026/27"


def retired(last=9, **extra):
    return {"season": SEASON, "last_gameweek": last, "reason": "beaten"} | extra


class TestTheRolesAsTheyStand(unittest.TestCase):
    def test_they_keep_the_rules(self):
        logged = [model for model, _ in entry.LOGGED_MODELS]
        self.assertEqual(roles.problems(logged=logged), [])

    def test_the_baseline_is_baseline_v1(self):
        self.assertEqual(roles.ROLES["baseline-v1"], roles.BASELINE)

    def test_the_committed_cumulative_scores_carry_the_roles_as_declared(self):
        """Regenerate scores/ (fpl-score) in the PR that changes a role."""
        cumulative = json.loads((ROOT / "scores" / "cumulative.json").read_text())
        for model, record in cumulative["models"].items():
            with self.subTest(model=model):
                self.assertEqual(record["role"], roles.role(model, SEASON))
                self.assertEqual(record["retired_after"], roles.last_gameweek(model, SEASON))


class TestTheRules(unittest.TestCase):
    def check(self, roles_, retired_=None, logged=None):
        return roles.problems(roles=roles_, retired=retired_ or {}, logged=logged)

    def test_there_must_be_one_baseline_and_one_main_model(self):
        self.assertEqual(len(self.check({"m-v1": roles.MAIN})), 1)
        self.assertEqual(len(self.check({"b-v1": roles.BASELINE})), 1)
        self.assertEqual(self.check({"b-v1": roles.BASELINE, "m-v1": roles.MAIN}), [])

    def test_two_challengers_are_one_too_many(self):
        found = self.check(
            {
                "b-v1": roles.BASELINE,
                "m-v1": roles.MAIN,
                "c-v1": roles.CHALLENGER,
                "d-v1": roles.CHALLENGER,
            }
        )
        self.assertEqual(len(found), 1)
        self.assertIn("challenger", found[0])

    def test_retiring_the_main_model_without_promoting_one_is_refused(self):
        found = self.check({"b-v1": roles.BASELINE, "c-v1": roles.CHALLENGER}, {"m-v1": retired()})
        self.assertIn("main", found[0])

    def test_a_retirement_needs_a_season_a_last_gameweek_and_a_reason(self):
        base = {"b-v1": roles.BASELINE, "m-v1": roles.MAIN}
        for bad in ({"last_gameweek": 9, "reason": "r"}, retired(last=True), retired(reason=""), 9):
            with self.subTest(retirement=bad):
                self.assertEqual(len(self.check(base, {"old-v1": bad})), 1)

    def test_a_model_cannot_be_logged_and_retired(self):
        found = self.check({"b-v1": roles.BASELINE, "m-v1": roles.MAIN}, {"m-v1": retired()})
        self.assertTrue(any("both" in f for f in found))

    def test_every_logged_model_has_a_role(self):
        found = self.check(
            {"b-v1": roles.BASELINE, "m-v1": roles.MAIN}, logged=["b-v1", "m-v1", "new-v1"]
        )
        self.assertEqual(len(found), 1)


class TestARetiredRecord(unittest.TestCase):
    def test_the_weeks_after_the_last_are_not_missed(self):
        settled = set(range(1, 15))
        self.assertEqual(missed_gameweeks([6, 8, 9], settled), [7, 10, 11, 12, 13, 14])
        self.assertEqual(missed_gameweeks([6, 8, 9], settled, last=9), [7])

    def test_a_retirement_is_for_its_own_season(self):
        with mock.patch.dict(roles.RETIRED, {"old-v1": retired()}):
            self.assertEqual(roles.role("old-v1", SEASON), roles.RETIRED_ROLE)
            self.assertEqual(roles.last_gameweek("old-v1", SEASON), 9)
            self.assertIsNone(roles.last_gameweek("old-v1", "2027/28"))
            self.assertIsNone(roles.role("old-v1", "2027/28"))

    def test_an_entry_after_a_retirement_contradicts_it(self):
        with mock.patch.dict(roles.RETIRED, {"old-v1": retired()}):
            found = roles.entries_after_retirement([(9, "old-v1"), (10, "old-v1")], SEASON)
        self.assertEqual(found, ["old-v1 retired after GW9 but has a GW10 entry"])


class TestEntryRefusesARetiredModel(fixtures.TempCwd):
    def test_a_retired_model_is_not_run(self):
        """Refused before anything is read: a dry run in a temporary directory, so a
        guard that broke could still write nothing to the real log."""
        with (
            mock.patch.dict(roles.RETIRED, {"baseline-v1": retired()}),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            entry.main(dry_run=True, models=entry.LOGGED_MODELS[:1])
        self.assertIn("Retired", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
