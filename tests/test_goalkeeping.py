"""Tests for #48: goals conceded and saves, priced from the fixture.

The scoring rules and the Poisson floor in fpl/models/features.py, then the
candidate for form-fixture-v2 (`goalkeeping=True`) on test_predict_fixture's
league with a keeper added to two clubs. Also #45's defensive contribution
(`defensive=True`), which is measured alongside it.

Run from the repository root: python -m unittest discover tests
"""

import math
import unittest
from unittest import mock

from fpl.models import features, predict_fixture
from tests.test_predict_fixture import League

SOFT = [(1, 3), (2, 4)]  # each strong side at home to a weak one
HARD = [(1, 2), (3, 4)]  # strong against strong, weak against weak


def brute_floor_mean(mean, per, terms=200):
    total, pmf = 0.0, math.exp(-mean)
    for k in range(terms):
        total += (k // per) * pmf
        pmf *= mean / (k + 1)
    return total


class TestRules(unittest.TestCase):

    def test_conceded_is_minus_one_per_two_for_keepers_and_defenders(self):
        self.assertEqual(features.conceded_points({"goals_conceded": 3}, "DEF"), -1)
        self.assertEqual(features.conceded_points({"goals_conceded": 4}, "GKP"), -2)
        self.assertEqual(features.conceded_points({"goals_conceded": 5}, "MID"), 0)

    def test_conceded_counts_at_any_minutes(self):
        """Not only 60+: the rule that reproduces total_points on real rows."""
        row = {"goals_conceded": 2, "minutes": 30}
        self.assertEqual(features.conceded_points(row, "DEF"), -1)

    def test_saves_are_one_per_three_for_keepers_only(self):
        self.assertEqual(features.save_points({"saves": 5}, "GKP"), 1)
        self.assertEqual(features.save_points({"saves": 6}, "GKP"), 2)
        self.assertEqual(features.save_points({"saves": 9}, "DEF"), 0)


class TestPoissonFloor(unittest.TestCase):

    def test_matches_the_distribution(self):
        for mean, per in ((1.3, 2), (2.9, 3), (0.2, 2), (6.0, 3)):
            with self.subTest(mean=mean, per=per):
                self.assertAlmostEqual(features.poisson_floor_mean(mean, per),
                                       brute_floor_mean(mean, per), places=9)

    def test_nothing_from_nothing(self):
        self.assertEqual(features.poisson_floor_mean(0.0, 2), 0.0)

    def test_below_the_mean_divided(self):
        """Flooring per match loses points that total / per would count."""
        self.assertLess(features.poisson_floor_mean(1.3, 2), 1.3 / 2)


class TestSeasonParts(unittest.TestCase):

    def test_priced_as_a_match_at_his_average(self):
        row = {"minutes": 3420, "goals_conceded": 38, "saves": 114}  # 38 full matches
        conceded, saves = features.season_goalkeeping_parts(row, "GKP")
        self.assertAlmostEqual(conceded, -features.poisson_floor_mean(1.0, 2))
        self.assertAlmostEqual(saves, features.poisson_floor_mean(3.0, 3))

    def test_outfield_and_unplayed_seasons_add_nothing(self):
        row = {"minutes": 3420, "goals_conceded": 38, "saves": 0}
        self.assertEqual(features.season_goalkeeping_parts(row, "MID"), (0.0, 0.0))
        self.assertEqual(features.season_goalkeeping_parts({"minutes": 0}, "GKP"), (0.0, 0.0))


class TestLeagueSaveRate(unittest.TestCase):

    def test_keepers_only_and_settled_rounds_before_the_target_only(self):
        elements = [{"id": 1, "element_type": 1}, {"id": 2, "element_type": 2}]
        positions = {1: "GKP", 2: "DEF"}
        histories = {
            1: [{"round": 1, "saves": 4, "expected_goals_conceded": "2.0"},
                {"round": 2, "saves": 2, "expected_goals_conceded": "1.0"},
                {"round": 3, "saves": 9, "expected_goals_conceded": "1.0"}],
            2: [{"round": 1, "saves": 5, "expected_goals_conceded": "1.0"}],
        }
        rate = features.league_save_rate(elements, positions, histories, 3, {1, 2, 3})
        self.assertAlmostEqual(rate, 6 / 3.0)
        rate = features.league_save_rate(elements, positions, histories, 3, {1})
        self.assertAlmostEqual(rate, 4 / 2.0)


class TestSplitParts(unittest.TestCase):

    def test_parts_and_rest_add_up_to_the_total(self):
        total, parts, rest = features.split_parts(
            (20, 6, 4, -2, 3), 450, (4.0, 1.0, 0.8, -0.4, 0.5), 270)
        self.assertAlmostEqual(sum(parts) + rest, total)
        self.assertEqual(len(parts), 4)


class KeeperLeague(League):
    """test_predict_fixture's league, with a keeper at clubs 1 and 3.

    Club 1's keeper faces little and saves little; club 3's faces a lot,
    concedes three a match and makes four saves.
    """

    PLAYERS = League.PLAYERS + [(9, 1, 1), (10, 3, 1)]

    def __init__(self):
        super().__init__()
        for row in self.histories[9]:
            row.update(points=6, saves=1, goals_conceded=0, expected_goals_conceded="0.3")
        for row in self.histories[10]:
            row.update(points=1, saves=4, goals_conceded=3, expected_goals_conceded="2.5")
        for pid in (2, 4, 6, 8):
            for row in self.histories[pid]:
                row.update(goals_conceded=0 if row["clean_sheets"] else 3)


class TestGoalkeeping(unittest.TestCase):

    def test_leaves_midfield_forwards_and_every_rate_alone(self):
        """Only GKP and DEF have these points, and the parts still add up."""
        league = KeeperLeague()
        before = league.predict(SOFT)
        after = league.predict(SOFT, goalkeeping=True)
        for pid, row in before.items():
            with self.subTest(player=pid):
                self.assertEqual(after[pid]["points_per_90"], row["points_per_90"])
                if row["position"] not in ("GKP", "DEF"):
                    self.assertEqual(after[pid]["predicted_points"], row["predicted_points"])

    def test_a_stronger_attack_costs_a_defender_more(self):
        league = KeeperLeague()

        def change(gw4):
            return (league.predict(gw4, goalkeeping=True)[2]["predicted_points"]
                    - league.predict(gw4)[2]["predicted_points"])

        self.assertLess(change(HARD), change(SOFT))

    def test_a_stronger_attack_means_more_saves(self):
        """With conceding switched off, what is left for a keeper is saves."""
        league = KeeperLeague()

        def change(gw4):
            return (league.predict(gw4, goalkeeping=True)[9]["predicted_points"]
                    - league.predict(gw4)[9]["predicted_points"])

        with mock.patch.dict(predict_fixture.CONCEDED_POINTS, clear=True):
            self.assertGreater(change(HARD), change(SOFT))

    def test_off_by_default(self):
        """form-fixture-v1 as logged is the default."""
        league = KeeperLeague()
        self.assertEqual(league.predict(SOFT),
                         league.predict(SOFT, goalkeeping=False, defensive=False))


class TestDefensiveContribution(unittest.TestCase):

    def test_no_threshold_no_points(self):
        self.assertEqual(features.defensive_points({"defensive_contribution": 30}, "GKP"), 0)
        self.assertEqual(features.chance_of_defensive_contribution(30.0, "GKP"), 0.0)

    def test_threshold_by_position(self):
        self.assertEqual(features.defensive_points({"defensive_contribution": 10}, "DEF"), 2)
        self.assertEqual(features.defensive_points({"defensive_contribution": 10}, "MID"), 0)
        self.assertEqual(features.defensive_points({"defensive_contribution": 12}, "MID"), 2)

    def test_count_rebuilt_as_his_position_now_counts_it(self):
        row = {"clearances_blocks_interceptions": 4, "tackles": 3, "recoveries": 6,
               "defensive_contribution": 13}
        self.assertEqual(features.defensive_count(row, "DEF"), 7)
        self.assertEqual(features.defensive_count(row, "MID"), 13)

    def test_a_keeper_earns_none(self):
        league = KeeperLeague()
        for pid in (9, 10):
            for row in league.histories[pid]:
                row["defensive_contribution"] = 20
        self.assertEqual(league.predict(SOFT, goalkeeping=True)[9],
                         league.predict(SOFT, goalkeeping=True, defensive=True)[9])

    def test_a_count_just_under_the_threshold_is_worth_something(self):
        """No DC points observed, but a chance of reaching 10 next time.

        In the rest alone he is worth nothing for it; read from his count,
        he is.
        """
        league = KeeperLeague()
        for row in league.histories[2]:
            row["defensive_contribution"] = 9
        without = league.predict(SOFT, goalkeeping=True)[2]["predicted_points"]
        with_dc = league.predict(SOFT, goalkeeping=True, defensive=True)[2]["predicted_points"]
        self.assertGreater(with_dc, without)


class TestDefensiveMinutes(unittest.TestCase):
    """The chance is convex in minutes, so it is taken at the minutes he plays."""

    def chance(self, count):
        return 2 * features.chance_of_defensive_contribution(count, "DEF")

    def test_ninety_or_nothing_keeps_the_full_match_chance_scaled(self):
        window = [{"minutes": 90}, {"minutes": 0}, {"minutes": 90}]
        self.assertAlmostEqual(
            features.defensive_contribution_points(9.0, "DEF", window, 60.0),
            self.chance(9.0) * 60 / 90)

    def test_sixty_every_match_is_judged_on_sixty_minutes(self):
        window = [{"minutes": 60}] * 3
        points = features.defensive_contribution_points(9.0, "DEF", window, 60.0)
        self.assertAlmostEqual(points, self.chance(6.0))
        self.assertLess(points, self.chance(9.0) * 60 / 90)

    def test_nothing_without_minutes(self):
        self.assertEqual(features.defensive_contribution_points(9.0, "DEF", [], 90.0), 0.0)
        self.assertEqual(
            features.defensive_contribution_points(9.0, "DEF", [{"minutes": 90}], 0.0), 0.0)


class TestLeagueRates(unittest.TestCase):

    def test_settled_rounds_before_the_target_only(self):
        """The rule 2 filter every league rate shares."""
        elements = [{"id": 1, "element_type": 2}]
        histories = {1: [{"round": 1, "minutes": 90, "goals_conceded": 2},
                         {"round": 2, "minutes": 90, "goals_conceded": 4},
                         {"round": 3, "minutes": 90, "goals_conceded": 6}]}
        rates = features.league_rates(elements, {2: "DEF"}, histories, 3, {1},
                                      (features.conceded_points,))
        self.assertEqual(rates, {"DEF": (-1.0,)})


class TestPriorParts(unittest.TestCase):

    def test_carry_goalkeeping_parts_from_the_same_row(self):
        past = [{"season_name": "2025/26", "minutes": 3420, "total_points": 140,
                 "goals_conceded": 38, "saves": 114, "clean_sheets": 12}]
        parts = features.player_prior_parts(past, "2025/26", "GKP", xg_weight=0)
        self.assertEqual((parts["conceded"], parts["saves"]),
                         features.season_goalkeeping_parts(past[0], "GKP"))


if __name__ == "__main__":
    unittest.main()
