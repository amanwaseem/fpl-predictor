"""Tests for fpl.db.stale: whether the database is behind the API (#93).

The comparison itself needs no database; the round trip through one is in
tests/test_db_load.py.
"""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

from fpl.db import stale
from tests.fixtures import element, event, team

EVENTS = [event(1, deadline="2026-08-14T17:30:00Z", finished=True, data_checked=True),
          event(2, deadline="2026-08-21T17:30:00Z")]
FIXTURES = [{"id": 1, "event": 1, "kickoff_time": "2026-08-15T14:00:00Z", "finished": True,
             "team_h": 1, "team_a": 2, "team_h_score": 2, "team_a_score": 1}]


def bootstrap():
    return {"events": EVENTS,
            "teams": [team(t) | {"code": 100 + t, "name": f"Team {t}"} for t in (1, 2)],
            "elements": [element(1), element(2, element_type=1)]}


class TestDifferences(unittest.TestCase):

    def test_the_api_against_itself_differs_in_nothing(self):
        api = stale.from_api(bootstrap(), FIXTURES)
        self.assertEqual(stale.differences(api, api), [])

    def test_a_settled_gameweek_the_database_has_not_seen_is_a_difference(self):
        api = stale.from_api(bootstrap(), FIXTURES)
        database = stale.from_api(bootstrap(), FIXTURES)
        deadline = datetime(2026, 8, 14, 17, 30, tzinfo=UTC)
        database["gameweeks"][1] = (deadline, True, False)
        self.assertEqual(stale.differences(api, database), ["gameweeks: 1 differ (1)"])

    def test_a_result_is_a_difference(self):
        api = stale.from_api(bootstrap(), FIXTURES)
        unplayed = [FIXTURES[0] | {"finished": False, "team_h_score": None,
                                   "team_a_score": None}]
        database = stale.from_api(bootstrap(), unplayed)
        self.assertEqual(stale.differences(api, database), ["fixtures: 1 differ (1)"])

    def test_a_new_player_is_a_difference_and_a_non_player_is_not(self):
        more = bootstrap()
        more["elements"] += [element(3), element(4, element_type=5)]
        found = stale.differences(stale.from_api(more, FIXTURES),
                                  stale.from_api(bootstrap(), FIXTURES))
        self.assertEqual(found, ["players: 1 differ (3)"])

    def test_an_empty_database_differs_everywhere(self):
        found = stale.differences(stale.from_api(bootstrap(), FIXTURES), {})
        self.assertEqual([line.split(":")[0] for line in found],
                         ["gameweeks", "fixtures", "teams", "players"])

    def test_many_differences_are_counted_not_listed(self):
        api = {"players": {i: ("x", "MID") for i in range(12)}}
        self.assertEqual(stale.differences(api, {}),
                         ["players: 12 differ (0, 1, 2, 3, 4 and 7 more)"])


class TestOutputs(unittest.TestCase):

    def test_the_step_output_is_written_for_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out"
            stale.write_output(True, {"GITHUB_OUTPUT": str(path)})
            stale.write_output(False, {"GITHUB_OUTPUT": str(path)})
            self.assertEqual(path.read_text(), "needed=true\nneeded=false\n")

    def test_outside_actions_nothing_is_written(self):
        stale.write_output(True, {})

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
