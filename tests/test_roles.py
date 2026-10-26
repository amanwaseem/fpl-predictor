"""The models' roles, fpl.log.roles (#109, ADR 0020)."""

import contextlib
import io
import unittest
from unittest import mock

from fpl.evaluation.score import missed_gameweeks
from fpl.log import entry, roles


class TestTheRolesAsTheyStand(unittest.TestCase):
    def test_they_keep_the_rules(self):
        logged = [model for model, _ in entry.LOGGED_MODELS]
        self.assertEqual(roles.problems(logged=logged), [])

    def test_the_baseline_is_baseline_v1(self):
        self.assertEqual(roles.ROLES["baseline-v1"], roles.BASELINE)


class TestTheRules(unittest.TestCase):
    def test_there_must_be_one_baseline(self):
        self.assertTrue(roles.problems(roles={"a-v1": roles.MAIN}, retired={}))

    def test_two_challengers_are_one_too_many(self):
        found = roles.problems(
            roles={"b-v1": roles.BASELINE, "c-v1": roles.CHALLENGER, "d-v1": roles.CHALLENGER},
            retired={},
        )
        self.assertEqual(len(found), 1)
        self.assertIn("challenger", found[0])

    def test_retiring_the_baseline_leaves_none(self):
        found = roles.problems(
            roles={"m-v1": roles.MAIN},
            retired={"b-v1": {"last_gameweek": 9, "reason": "beaten"}},
        )
        self.assertIn("baseline", found[0])

    def test_a_retirement_needs_its_last_gameweek_and_a_reason(self):
        found = roles.problems(
            roles={"b-v1": roles.BASELINE}, retired={"old-v1": {"last_gameweek": 9}}
        )
        self.assertEqual(len(found), 1)

    def test_a_model_cannot_be_logged_and_retired(self):
        found = roles.problems(
            roles={"b-v1": roles.BASELINE, "m-v1": roles.MAIN},
            retired={"m-v1": {"last_gameweek": 9, "reason": "beaten"}},
        )
        self.assertTrue(any("both" in f for f in found))

    def test_every_logged_model_has_a_role(self):
        found = roles.problems(
            roles={"b-v1": roles.BASELINE}, retired={}, logged=["b-v1", "new-v1"]
        )
        self.assertEqual(len(found), 1)


class TestARetiredRecord(unittest.TestCase):
    def test_the_weeks_after_the_last_are_not_missed(self):
        settled = set(range(1, 15))
        self.assertEqual(missed_gameweeks([6, 8, 9], settled), [7, 10, 11, 12, 13, 14])
        self.assertEqual(missed_gameweeks([6, 8, 9], settled, last=9), [7])

    def test_a_retired_model_is_not_run(self):
        retired = {"baseline-v1": {"last_gameweek": 9, "reason": "a test"}}
        with (
            mock.patch.dict(roles.RETIRED, retired),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            entry.main(models=entry.LOGGED_MODELS[:1])
        self.assertIn("Retired", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
