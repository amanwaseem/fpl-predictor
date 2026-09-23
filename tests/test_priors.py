"""Tests for the priors in fpl/features.py and the candidate that isolates them.

Run from the repository root: python -m unittest discover tests
"""

import unittest

from fpl import features, predict_baseline
from fpl.candidates import baseline_prior_rows
from fpl.snapshot import usable_rounds
from tests import fixtures

EVENTS = fixtures.season(4, fixtures.future_deadline())  # GW1 in August 2026


def past(season, minutes, points):
    return {"season_name": season, "minutes": minutes, "total_points": points}


class TestPreviousSeason(unittest.TestCase):

    def test_read_from_the_gw1_deadline(self):
        self.assertEqual(features.previous_season(EVENTS), "2025/26")

    def test_no_gw1_stops_the_run(self):
        with self.assertRaises(SystemExit):
            features.previous_season([e for e in EVENTS if e["id"] != 1])


class TestPlayerPrior(unittest.TestCase):

    def test_points_per_90_from_the_named_season(self):
        self.assertAlmostEqual(
            features.player_prior([past("2025/26", 2700, 150)], "2025/26"), 5.0)

    def test_an_older_season_is_not_last_season(self):
        """Absent from the league last year: his last row is two seasons old."""
        self.assertIsNone(features.player_prior([past("2024/25", 3000, 200)], "2025/26"))

    def test_this_season_is_never_read(self):
        """A snapshot taken after the season ends lists it in history_past."""
        rows = [past("2025/26", 3000, 100), past("2026/27", 3000, 300)]
        self.assertAlmostEqual(features.player_prior(rows, "2025/26"), 3.0)

    def test_below_the_floor_is_no_prior(self):
        floor = features.PLAYER_PRIOR_FLOOR_MINUTES
        self.assertIsNone(features.player_prior([past("2025/26", floor - 1, 90)], "2025/26"))
        self.assertIsNotNone(features.player_prior([past("2025/26", floor, 90)], "2025/26"))


class TestPrior(unittest.TestCase):

    def test_no_last_season_falls_back_to_position(self):
        self.assertEqual(features.prior("DEF", None, 5000),
                         (features.POSITION_PRIOR_PP90["DEF"], features.POSITION_PRIOR_MINUTES))

    def test_last_season_fades_as_this_one_accumulates(self):
        full = features.PLAYER_PRIOR_MINUTES
        half_life = features.PLAYER_PRIOR_HALF_LIFE_MINUTES
        self.assertEqual(features.prior("MID", 6.0, 0), (6.0, full))
        self.assertAlmostEqual(features.prior("MID", 6.0, half_life)[1], full / 2)

    def test_shrunk_pp90(self):
        # 270 observed minutes at 9.0/90 against 270 pseudo-minutes at 3.0/90.
        self.assertAlmostEqual(features.shrunk_pp90(27, 270, 3.0, 270), 6.0)

    def test_season_minutes_skips_the_target_and_unsettled_rounds(self):
        history = fixtures.history([1, 2, 3, 4], minutes=90)
        self.assertEqual(features.season_minutes(history, 4, {1, 3}), 180)


class TestCandidate(unittest.TestCase):

    def setUp(self):
        self.teams = [fixtures.team(t) for t in (1, 2)]
        self.elements = [fixtures.element(1, team=1), fixtures.element(2, team=2)]
        self.bootstrap = {"elements": self.elements, "teams": self.teams,
                          "element_types": fixtures.ELEMENT_TYPES, "events": EVENTS}
        self.fixtures = [fixtures.fixture(4, 1, 2)]
        self.histories = {1: fixtures.history([1, 2, 3], points=2),
                          2: fixtures.history([1, 2, 3], points=2)}

    def rows(self, pasts):
        return baseline_prior_rows(self.bootstrap, self.fixtures, self.histories, 4, pasts)

    def test_without_last_season_it_is_baseline_v1(self):
        """The fallback is baseline-v1 exactly, so a backtest gap is the prior's."""
        usable, _ = usable_rounds(EVENTS, 4)
        self.assertEqual(self.rows({}), predict_baseline.predict_rows(
            self.bootstrap, self.fixtures, self.histories, 4, usable))

    def test_a_strong_last_season_lifts_an_identical_start(self):
        rows = self.rows({1: [past("2025/26", 3000, 250)]})
        self.assertEqual(rows[0]["player_id"], 1)
        self.assertGreater(rows[0]["predicted_points"], rows[1]["predicted_points"])

    def test_a_double_reads_minutes_per_fixture(self):
        self.histories[1] = fixtures.history([1, 2, 3], minutes=90) + [
            {"round": 3, "minutes": 90, "total_points": 2}]
        row = next(r for r in self.rows({}) if r["player_id"] == 1)
        self.assertEqual(row["expected_minutes"], 90.0)


if __name__ == "__main__":
    unittest.main()
