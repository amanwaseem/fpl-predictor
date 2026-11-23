"""Live points from FPL's live and fixtures endpoints, fpl.api.live (#199).

The JSON under fpl_live/ was recorded from the FPL API on 2026-11-22, during
GW11, and trimmed to eight players: nine matches over (bonus not yet
confirmed) and one still to kick off. A mid-match state is made from it by
re-opening one fixture, as FPL showed it a few hours earlier.
"""

import copy
import json
import unittest
from pathlib import Path

from fastapi import HTTPException

from fpl.api import live

HERE = Path(__file__).parent / "fpl_live"
LIVE = json.loads((HERE / "event_11_live.json").read_text())
FIXTURES = json.loads((HERE / "fixtures_event_11.json").read_text())
NAMES = {15: "MCI", 10: "FUL", 4: "BRE", 9: "EVE"}


def picks(*elements, chip=None, cost=0):
    """FPL's picks: the first two start, the captain first; the rest on the bench."""
    return {
        "active_chip": chip,
        "entry_history": {"event_transfers_cost": cost},
        "picks": [
            {
                "element": e,
                "position": i + 1,
                "multiplier": (2 if i == 0 else 1) if i < 2 else 0,
                "is_captain": i == 0,
                "is_vice_captain": i == 1,
            }
            for i, e in enumerate(elements)
        ],
    }


class TestSummarise(unittest.TestCase):
    def test_matches_over_are_finished_before_bonus_is_confirmed(self):
        out = live.summarise(11, LIVE, FIXTURES, NAMES)
        by_id = {m["fixture_id"]: m for m in out["fixtures"]}
        self.assertEqual(
            {k: by_id[108][k] for k in ("home", "away", "home_score", "away_score", "state")},
            {"home": "MCI", "away": "FUL", "home_score": 1, "away_score": 1, "state": "finished"},
        )
        self.assertEqual(by_id[103]["state"], "not_started")
        self.assertFalse(out["in_play"])

    def test_a_match_under_way_is_live_with_its_minute(self):
        fixtures = copy.deepcopy(FIXTURES)
        playing = next(f for f in fixtures if f["id"] == 108)
        playing |= {"finished_provisional": False, "minutes": 63}
        out = live.summarise(11, LIVE, fixtures, NAMES)
        match = next(m for m in out["fixtures"] if m["fixture_id"] == 108)
        self.assertEqual((match["state"], match["minutes"]), ("live", 63))
        self.assertTrue(out["in_play"])

    def test_matches_come_in_kick_off_order(self):
        kickoffs = [m["kickoff_utc"] for m in live.summarise(11, LIVE, FIXTURES, NAMES)["fixtures"]]
        self.assertEqual(kickoffs, sorted(kickoffs))

    def test_every_player_has_points_and_minutes(self):
        players = live.summarise(11, LIVE, FIXTURES, NAMES)["players"]
        self.assertEqual(len(players), 8)
        self.assertEqual(players[0], {"player_id": 1, "minutes": 69, "total_points": 1, "bonus": 0})

    def test_a_team_scores_its_picks_times_their_multipliers_less_its_hits(self):
        out = live.summarise(11, LIVE, FIXTURES, NAMES, picks(4, 5, 1, cost=4), team_id=7)
        team = out["team"]
        # Captain 4 (1 point, doubled), 5 (1 point), 1 on the bench; 4 for a hit.
        self.assertEqual((team["points"], team["transfers_cost"]), (2 + 1 - 4, 4))
        self.assertEqual([p["multiplier"] for p in team["picks"]], [2, 1, 0])

    def test_a_changed_schema_is_fpls_problem(self):
        for broken in ({}, {"elements": [{"id": 1}]}):
            with self.subTest(live=broken), self.assertRaises(HTTPException) as caught:
                live.summarise(11, broken, FIXTURES, NAMES)
            self.assertEqual(caught.exception.status_code, 502)
