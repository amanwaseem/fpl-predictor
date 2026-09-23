"""Tests for fpl/teams.py, and the candidate and backtest measure that use it.

Run from the repository root: python -m unittest discover tests
"""

import math
import unittest

from fpl import backtest, teams
from fpl.candidates import baseline_fixture_rows, split_rates
from fpl.features import shrunk_pp90
from tests import fixtures

EVENTS = fixtures.season(4, fixtures.future_deadline())


def played(fid, gw, home, away, score_h, score_a, **extra):
    return dict({"id": fid, "event": gw, "team_h": home, "team_a": away,
                 "team_h_score": score_h, "team_a_score": score_a, "finished": True},
                **extra)


def row(fid, gw, home, xg, **extra):
    return dict({"fixture": fid, "round": gw, "was_home": home, "minutes": 90,
                 "total_points": 2, "expected_goals": f"{xg:.2f}"}, **extra)


def match(team, opponent, xg_for, home=True, fixture=1):
    return {"fixture": fixture, "team": team, "opponent": opponent, "home": home,
            "xg_for": xg_for, "xg_against": 0.0, "goals_for": 0, "goals_against": 0}


class TestTeamMatches(unittest.TestCase):

    def test_xg_is_summed_per_club_and_goals_come_from_the_score(self):
        fx = [played(1, 1, 1, 2, 2, 0)]
        histories = {10: [row(1, 1, True, 0.8)], 11: [row(1, 1, True, 0.7)],
                     20: [row(1, 1, False, 0.4)]}
        home, away = sorted(teams.team_matches(fx, histories, 2, {1}),
                            key=lambda m: not m["home"])
        self.assertEqual((home["team"], home["opponent"], home["goals_for"]), (1, 2, 2))
        self.assertAlmostEqual(home["xg_for"], 1.5)
        self.assertAlmostEqual(home["xg_against"], 0.4)
        self.assertAlmostEqual(away["xg_for"], 0.4)
        self.assertEqual(away["goals_against"], 2)

    def test_only_settled_rounds_before_the_target(self):
        fx = [played(1, 1, 1, 2, 1, 0), played(2, 2, 1, 2, 1, 0),
              played(3, 3, 1, 2, 1, 0), dict(played(4, 1, 3, 4, 0, 0), finished=False)]
        histories = {10: [row(f, f, True, 1.0) for f in (1, 2, 3)]}
        fixtures_seen = {m["fixture"] for m in teams.team_matches(fx, histories, 3, {1})}
        self.assertEqual(fixtures_seen, {1})  # 2 unsettled, 3 is the target, 4 unfinished


class TestRatings(unittest.TestCase):

    def test_an_even_league_rates_everyone_average(self):
        matches = [match(1, 2, 1.2), match(2, 1, 1.2, home=False)]
        ratings = teams.Ratings(matches)
        self.assertAlmostEqual(ratings.rate, 1.2)
        self.assertAlmostEqual(ratings.attack[1], 1.0)
        self.assertAlmostEqual(ratings.expected_goals(1, 2, True), 1.2)

    def test_a_side_that_creates_more_rates_above_average(self):
        matches = [match(1, 2, 2.0), match(2, 1, 0.5, home=False)]
        ratings = teams.Ratings(matches, prior_matches=0)
        self.assertGreater(ratings.attack[1], 1.0)
        self.assertGreater(ratings.expected_goals(1, 2, True),
                           ratings.expected_goals(2, 1, False))

    def test_the_fit_adjusts_for_opposition(self):
        """Same xG, but club 1 made it against the meaner defence.

        Clubs 5 and 6 each face both 3 and 4, and both find 3 harder to
        create against. That is what tells the fit 3 defends well rather than
        that 1 attacks badly.
        """
        matches = [match(1, 3, 1.0), match(2, 4, 1.0),
                   match(5, 3, 0.5), match(5, 4, 1.5),
                   match(6, 3, 0.5), match(6, 4, 1.5)]
        matches += [match(m["opponent"], m["team"], 1.0, home=False) for m in list(matches)]
        ratings = teams.Ratings(matches, prior_matches=0)
        self.assertGreater(ratings.attack[1], ratings.attack[2])

    def test_shrinkage_pulls_toward_average(self):
        matches = [match(1, 2, 3.0), match(2, 1, 0.5, home=False)]
        loose = teams.Ratings(matches, prior_matches=0).attack[1]
        tight = teams.Ratings(matches, prior_matches=10).attack[1]
        self.assertGreater(loose, tight)
        self.assertGreater(tight, 1.0)

    def test_home_advantage(self):
        ratings = teams.Ratings([match(1, 2, 1.0), match(2, 1, 1.0, home=False)],
                                home_advantage=1.2)
        self.assertGreater(ratings.expected_goals(1, 2, True),
                           ratings.expected_goals(1, 2, False))

    def test_unrated_clubs_are_average(self):
        ratings = teams.Ratings([match(1, 2, 1.0), match(2, 1, 1.0, home=False)])
        self.assertAlmostEqual(ratings.expected_goals(7, 8, True), ratings.rate)

    def test_a_league_without_xg_stops_the_run(self):
        """Zero would make every clean sheet certain, not leave them unpredicted."""
        for matches in ([], [match(1, 2, 0.0), match(2, 1, 0.0, home=False)]):
            with self.subTest(matches=matches), self.assertRaises(SystemExit):
                teams.Ratings(matches)

    def test_fixture_goals_reads_both_sides(self):
        ratings = teams.Ratings([match(1, 2, 2.0), match(2, 1, 0.5, home=False)],
                                prior_matches=0)
        fixture = {"team_h": 2, "team_a": 1}
        self.assertEqual(ratings.fixture_goals(fixture, 1),
                         (ratings.expected_goals(1, 2, False),
                          ratings.expected_goals(2, 1, True)))


class TestDifficulty(unittest.TestCase):

    def test_sides_facing_harder_fixtures_are_expected_to_score_less(self):
        fx = [{"id": 1, "team_h": 1, "team_a": 2, "team_h_difficulty": 2,
               "team_a_difficulty": 5}]
        matches = [match(1, 2, 2.0), match(2, 1, 0.4, home=False)]
        ratings = teams.DifficultyRatings(matches, fx, prior_matches=0)
        goals_for, goals_against = ratings.fixture_goals(fx[0], 1)
        self.assertGreater(goals_for, goals_against)
        self.assertEqual(ratings.usual_goals(1), ratings.rate)


class TestCleanSheet(unittest.TestCase):

    def test_poisson_zero(self):
        self.assertEqual(teams.clean_sheet_probability(0.0), 1.0)
        self.assertAlmostEqual(teams.clean_sheet_probability(1.2), math.exp(-1.2))


class TestCandidate(unittest.TestCase):
    """Clubs 1 and 2 have beaten 3 and 4 every week on big xG.

    The same players are predicted twice for GW4, against a weak side and
    against a strong one, so the opponent is the only thing that differs.
    """

    def setUp(self):
        self.bootstrap = {
            "elements": [fixtures.element(pid, team=club, element_type=pos)
                         for pid, club, pos in ((1, 1, 4), (2, 1, 2), (3, 3, 3),
                                                (4, 2, 4), (5, 4, 3))],
            "teams": [fixtures.team(t) for t in (1, 2, 3, 4)],
            "element_types": fixtures.ELEMENT_TYPES, "events": EVENTS,
        }
        self.history_fixtures, self.histories = [], {p: [] for p in range(1, 6)}
        for gw in (1, 2, 3):
            for fid, home, away, home_pids, away_pids in (
                    (gw * 10, 1, 3, (1, 2), (3,)), (gw * 10 + 1, 2, 4, (4,), (5,))):
                self.history_fixtures.append(played(fid, gw, home, away, 3, 0))
                for pid in home_pids:
                    self.histories[pid].append(row(fid, gw, True, 1.5, goals_scored=1,
                                                   clean_sheets=1))
                for pid in away_pids:
                    self.histories[pid].append(row(fid, gw, False, 0.1))

    def predict(self, opponent):
        """Club 1 hosts `opponent` in GW4; the other two clubs meet each other."""
        others = sorted({2, 3, 4} - {opponent})
        fx = self.history_fixtures + [
            {"id": 40, "event": 4, "team_h": 1, "team_a": opponent},
            {"id": 41, "event": 4, "team_h": others[0], "team_a": others[1]}]
        return {r["player_id"]: r["predicted_points"] for r in baseline_fixture_rows(
            self.bootstrap, fx, self.histories, 4, {})}

    def test_a_weak_opponent_lifts_attack_and_clean_sheet(self):
        weak, strong = self.predict(3), self.predict(2)
        self.assertGreater(weak[1], strong[1])  # forward: attacking returns
        self.assertGreater(weak[2], strong[2])  # defender: clean sheet

    def test_a_blank_predicts_nothing(self):
        fx = self.history_fixtures + [{"id": 41, "event": 4, "team_h": 2, "team_a": 4}]
        rows = {r["player_id"]: r for r in baseline_fixture_rows(
            self.bootstrap, fx, self.histories, 4, {})}
        self.assertEqual(rows[1]["predicted_points"], 0.0)


class TestSplitRates(unittest.TestCase):
    """The parts of a player's rate have to add back up to it (PR #40 review)."""

    def test_the_rest_is_a_shrunk_rate_not_a_leftover(self):
        observed, priors = (70, 56, 0), (4.0, 1.2, 0.3)
        total, attack, cs, rest = split_rates(observed, 450, priors, 1800)
        self.assertAlmostEqual(total, attack + cs + rest)
        self.assertAlmostEqual(rest, shrunk_pp90(70 - 56 - 0, 450, 4.0 - 1.2 - 0.3, 1800))

    def test_a_hot_start_on_a_modest_prior_keeps_a_positive_rest(self):
        """The review's midfielder: 10 goals and 70 points in 450 minutes.

        Shrunk toward last season at 1800 for the total but toward the league
        at 270 for attack, his rest came out near -0.7.
        """
        _, _, _, rest = split_rates((70, 50, 0), 450, (4.0, 1.2, 0.3), 1800)
        self.assertGreater(rest, 0)


class TestClubSpread(unittest.TestCase):

    def test_residuals_sum_per_club_over_players_predicted_to_play(self):
        matched = [
            {"team": "A", "expected_minutes": 90, "predicted_points": 5, "actual_points": 2},
            {"team": "A", "expected_minutes": 90, "predicted_points": 1, "actual_points": 2},
            {"team": "B", "expected_minutes": 90, "predicted_points": 2, "actual_points": 6},
            {"team": "B", "expected_minutes": 0, "predicted_points": 0, "actual_points": 9},
        ]
        self.assertEqual(backtest.club_residuals(matched), {"A": 2, "B": -4})

    def test_spread_ignores_a_shift_common_to_every_club(self):
        self.assertAlmostEqual(backtest.spread([2, -4]), 3.0)
        self.assertAlmostEqual(backtest.spread([12, 6]), 3.0)
        self.assertIsNone(backtest.spread([]))


if __name__ == "__main__":
    unittest.main()
