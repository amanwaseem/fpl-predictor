"""Tests for #49's candidate: the flat positional prior, moved by price.

The fit and the prior in fpl/candidates.py, the positional_prior hook in
fpl.predict_fixture they plug into, and the control that isolates what price
itself is worth. That neither candidate sees past the deadline is
tests/test_backtest.py's poison test, which runs every model it registers.

Run from the repository root: python -m unittest discover tests
"""

import unittest

from fpl import candidates, features
from fpl.snapshot import usable_rounds
from tests import fixtures
from tests.test_predict_fixture import EVENTS, League

SOFT = [(1, 3), (2, 4)]


def past(minutes=3000, total_points=150, **fields):
    return [dict({"season_name": "2025/26", "minutes": minutes,
                  "total_points": total_points, "goals_scored": 0, "assists": 0,
                  "clean_sheets": 0}, **fields)]


class TestSlope(unittest.TestCase):

    def test_least_squares(self):
        self.assertAlmostEqual(candidates.slope([(4, 3.0), (5, 3.5), (6, 4.0)]), 0.5)

    def test_flat_when_price_never_varies(self):
        self.assertEqual(candidates.slope([(5, 3.0), (5, 4.0)]), 0.0)


class TestPriceFit(unittest.TestCase):

    def bootstrap(self, n):
        return {"element_types": fixtures.ELEMENT_TYPES, "events": EVENTS,
                "elements": [fixtures.element(pid, element_type=3, now_cost=50 + 10 * pid)
                             for pid in range(1, n + 1)]}

    def test_fitted_on_usable_seasons_centred_on_the_rest(self):
        bootstrap = self.bootstrap(7)
        # £6m to £10m earned 3 to 5 per 90. £11m and £12m have no usable
        # season: they are left out of the fit, and the centre is theirs.
        pasts = {pid: past(minutes=2700, total_points=int(30 * (3 + (pid - 1) * 0.5)))
                 for pid in range(1, 6)}
        pasts[6] = past(minutes=900, total_points=0)
        slopes, centres = candidates.price_fit(bootstrap, pasts)
        self.assertAlmostEqual(slopes["MID"][0], 0.5)
        self.assertAlmostEqual(centres["MID"], 11.5)

    def test_the_models_floor_decides_who_is_fitted(self):
        bootstrap = self.bootstrap(6)
        pasts = {pid: past(minutes=900 * pid, total_points=10 * pid) for pid in range(1, 7)}
        slopes, _ = candidates.price_fit(bootstrap, pasts)
        self.assertEqual(slopes, {}, "only 3 seasons reach 2700 minutes")
        slopes, _ = candidates.price_fit(bootstrap, pasts, floor=900)
        self.assertNotIn("MID", slopes, "all six fit, and none is left to apply it to")

    def test_too_few_players_and_the_position_keeps_its_flat_prior(self):
        bootstrap = self.bootstrap(candidates.PRICE_FIT_MIN_PLAYERS)
        pasts = {e["id"]: past() for e in bootstrap["elements"][:-1]}
        slopes, _ = candidates.price_fit(bootstrap, pasts)
        self.assertEqual(slopes, {})


class TestPricePrior(unittest.TestCase):
    FLAT = (3.6, 1.0, 0.3)
    SLOPES = {"MID": (0.5, 0.4, 0.0)}
    CENTRES = {"MID": 6.0}

    def prior(self, now_cost, position="MID", minutes=0):
        hook = candidates.price_prior(self.SLOPES, self.CENTRES, 900)
        return hook({"now_cost": now_cost}, position, self.FLAT, 270, minutes)

    def test_the_mean_price_keeps_the_flat_prior(self):
        priors, weight = self.prior(60)
        for got, want in zip(priors, self.FLAT, strict=True):
            self.assertAlmostEqual(got, want)
        self.assertEqual(weight, 900)

    def test_a_dearer_player_is_moved_up_by_each_parts_slope(self):
        priors, _ = self.prior(80)
        for got, want in zip(priors, (4.6, 1.8, 0.3), strict=True):
            self.assertAlmostEqual(got, want)

    def test_the_parts_never_add_up_to_more_than_the_total(self):
        """Each part floors at zero, the rest included, and the total is their sum."""
        for now_cost in (-200, -20, 20, 60, 200):
            with self.subTest(now_cost=now_cost):
                (total, attack, clean_sheet), _ = self.prior(now_cost)
                self.assertGreaterEqual(min(attack, clean_sheet), 0.0)
                self.assertGreaterEqual(total - attack - clean_sheet, -1e-12)

    def test_the_weight_fades_with_this_seasons_minutes(self):
        _, early = self.prior(60)
        _, later = self.prior(60, minutes=features.PLAYER_PRIOR_HALF_LIFE_MINUTES)
        self.assertAlmostEqual(later, early / 2)

    def test_an_unfitted_position_keeps_its_flat_prior_at_the_weight(self):
        self.assertEqual(self.prior(80, position="FWD"), (self.FLAT, 900))


class TestHook(unittest.TestCase):

    def test_not_called_for_a_player_with_a_usable_last_season(self):
        league = League()
        league.pasts[1] = past(goals_scored=20, expected_goals="18.0",
                               expected_assists="4.0")
        called = []
        league.predict(SOFT, positional_prior=lambda pl, pos, pr, m, _: (
            called.append(pl["id"]), (pr, m))[1])
        self.assertNotIn(1, called)
        self.assertIn(3, called)

    def test_a_dearer_squad_player_is_predicted_higher(self):
        league = League()
        cheap = league.predict(SOFT, positional_prior=candidates.price_prior(
            {"FWD": (1.0, 0.5, 0.0)}, {"FWD": 7.0}, 270))
        league.elements = [dict(e, now_cost=90) if e["id"] == 3 else e
                           for e in league.elements]
        dear = league.predict(SOFT, positional_prior=candidates.price_prior(
            {"FWD": (1.0, 0.5, 0.0)}, {"FWD": 7.0}, 270))
        self.assertGreater(dear[3]["predicted_points"], cheap[3]["predicted_points"])


class TestControl(unittest.TestCase):

    def test_moves_only_players_on_the_positional_prior(self):
        """flat-prior-weight reweights the flat prior and touches nothing else."""
        league = League()
        league.pasts[1] = past(goals_scored=20, expected_goals="18.0", expected_assists="4.0")
        usable, _ = usable_rounds(EVENTS, 4)
        fx = league.history_fixtures + [{"id": 40, "event": 4, "team_h": 1, "team_a": 3},
                                        {"id": 41, "event": 4, "team_h": 2, "team_a": 4}]
        control = {r["player_id"]: r for r in candidates.flat_prior_rows(
            league.bootstrap(), fx, league.histories, 4, league.pasts,
            price_prior_minutes=3600)}
        v2 = {r["player_id"]: r for r in candidates.fixture_rows(
            league.bootstrap(), fx, league.histories, league.pasts, 4, usable,
            goalkeeping=True)}
        self.assertEqual(control[1], v2[1])
        self.assertNotEqual(control[3], v2[3])


if __name__ == "__main__":
    unittest.main()
