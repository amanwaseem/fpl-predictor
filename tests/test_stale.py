"""Tests for fpl.db.stale: whether the database is behind the API (#93).

The comparison itself needs no database; the round trip through one is in
tests/test_db_load.py.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

from fpl.db import stale
from fpl.db.migrate import head
from tests.fixtures import element, event, team

EVENTS = [event(1, deadline="2026-08-14T17:30:00Z", finished=True, data_checked=True),
          event(2, deadline="2026-08-21T17:30:00Z")]
FIXTURES = [{"id": 1, "event": 1, "kickoff_time": "2026-08-15T14:00:00Z", "finished": True,
             "team_h": 1, "team_a": 2, "team_h_score": 2, "team_a_score": 1}]


def player(pid, **kwargs):
    return element(pid, **kwargs) | {"code": 1000 + pid}


def bootstrap():
    return {"events": EVENTS,
            "teams": [team(t) | {"code": 100 + t, "name": f"Team {t}"} for t in (1, 2)],
            "elements": [player(1), player(2, element_type=1)]}


class TestDifferences(unittest.TestCase):

    def test_the_api_against_itself_differs_in_nothing(self):
        api = stale.from_api(bootstrap(), FIXTURES)
        self.assertEqual(stale.differences(api, api), [])

    def test_a_settled_gameweek_the_database_has_not_seen_is_a_difference(self):
        api = stale.from_api(bootstrap(), FIXTURES)
        database = stale.from_api(bootstrap(), FIXTURES)
        deadline = datetime(2026, 8, 14, 17, 30, tzinfo=UTC)
        database["gameweeks"][(1,)] = (deadline, True, False)
        self.assertEqual(stale.differences(api, database), ["gameweeks: 1 differ (1)"])

    def test_a_result_is_a_difference(self):
        api = stale.from_api(bootstrap(), FIXTURES)
        unplayed = [FIXTURES[0] | {"finished": False, "team_h_score": None,
                                   "team_a_score": None}]
        database = stale.from_api(bootstrap(), unplayed)
        self.assertEqual(stale.differences(api, database), ["fixtures: 1 differ (1)"])

    def test_a_new_player_is_a_difference_and_a_non_player_is_not(self):
        more = bootstrap()
        more["elements"] += [player(3), player(4, element_type=5)]
        found = stale.differences(stale.from_api(more, FIXTURES),
                                  stale.from_api(bootstrap(), FIXTURES))
        self.assertEqual(found, ["players: 1 differ (3)", "clubs: 1 differ (3)"])

    def test_an_empty_database_differs_everywhere(self):
        found = stale.differences(stale.from_api(bootstrap(), FIXTURES), {})
        self.assertEqual([line.split(":")[0] for line in found],
                         ["gameweeks", "fixtures", "teams", "players", "clubs"])

    def test_a_transfer_is_a_difference(self):
        moved = bootstrap()
        moved["elements"][0] = player(1, team=2)
        found = stale.differences(stale.from_api(moved, FIXTURES),
                                  stale.from_api(bootstrap(), FIXTURES))
        self.assertEqual(found, ["clubs: 1 differ (1)"])

    def test_clubs_are_not_compared_once_the_season_is_over(self):
        over = bootstrap() | {"events": [e | {"finished": True} for e in EVENTS]}
        self.assertNotIn("clubs", stale.from_api(over, FIXTURES))

    def test_a_home_and_away_swap_is_a_difference(self):
        swapped = [FIXTURES[0] | {"team_h": 2, "team_a": 1}]
        found = stale.differences(stale.from_api(bootstrap(), swapped),
                                  stale.from_api(bootstrap(), FIXTURES))
        self.assertEqual(found, ["fixtures: 1 differ (1)"])

    def test_a_row_only_the_database_has_is_not_a_difference(self):
        """The load never deletes, so it would be a difference every day."""
        fewer = bootstrap()
        fewer["elements"] = fewer["elements"][:1]
        found = stale.differences(stale.from_api(fewer, FIXTURES),
                                  stale.from_api(bootstrap(), FIXTURES))
        self.assertEqual(found, [])

    def test_many_differences_are_counted_not_listed(self):
        api = {"players": {(i,): ("x", "MID") for i in range(12)}}
        self.assertEqual(stale.differences(api, {}),
                         ["players: 12 differ (0, 1, 2, 3, 4 and 7 more)"])


class TestSchema(unittest.TestCase):

    def test_at_head_is_fine(self):
        self.assertIsNone(stale.schema_problem(head(), head()))

    def test_behind_waits_for_the_deploy(self):
        self.assertIn("waits for the deploy", stale.schema_problem(None, head()))

    def test_ahead_says_main_has_moved_on(self):
        self.assertIn("main has moved on", stale.schema_problem("ffff9999", head()))


class TestOutputs(unittest.TestCase):

    def test_the_step_output_is_written_for_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out"
            stale.write_output(True, {"GITHUB_OUTPUT": str(path)})
            stale.write_output(False, {"GITHUB_OUTPUT": str(path)})
            self.assertEqual(path.read_text(), "needed=true\nneeded=false\n")

    def test_outside_actions_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                stale.write_output(True, {})
                self.assertEqual(os.listdir(tmp), [])
            finally:
                os.chdir(cwd)

    def test_the_newest_complete_snapshot_is_compared_not_latest(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            raw = Path(tmp)
            for name, complete in (("20261020T000000Z", True), ("20261021T000000Z", True),
                                   ("20261022T000000Z", False)):
                (raw / name).mkdir()
                if complete:
                    (raw / name / "manifest.json").write_text("{}")
            (raw / "LATEST").write_text("20261020T000000Z")
            original, stale.RAW = stale.RAW, raw
            try:
                self.assertEqual(stale.newest_snapshot().name, "20261021T000000Z")
            finally:
                stale.RAW = original


if __name__ == "__main__":
    unittest.main()
