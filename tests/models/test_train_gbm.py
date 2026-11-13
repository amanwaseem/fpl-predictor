"""Pieces of fpl.models.train_gbm that need no database or LightGBM (#85)."""

import unittest

from fpl.models import train_gbm


class TestBootstrap(unittest.TestCase):
    def test_the_tails_are_the_same_size(self):
        """With draws whose statistic is 1, 2, ... draws, the interval is the
        2.5th and 97.5th percentiles exactly: as many draws below as above."""
        draws, counter = 400, iter(range(1, 401))
        low, high = train_gbm.bootstrap(
            {1: [0], 2: [0]}, {"x": lambda sample: next(counter)}, draws=draws
        )["x"]
        tail = int(0.025 * draws)
        self.assertEqual((low, high), (1 + tail, draws - tail))

    def test_every_statistic_comes_from_the_same_draws(self):
        by_gameweek = {gw: [gw] for gw in range(1, 11)}
        ci = train_gbm.bootstrap(
            by_gameweek, {"a": lambda s: sum(s), "b": lambda s: sum(s) * 2}, draws=200
        )
        self.assertEqual((ci["a"][0] * 2, ci["a"][1] * 2), ci["b"])


class TestUndefinedStatistics(unittest.TestCase):
    def test_a_statistic_with_no_value_on_some_draws_is_left_out_of_them(self):
        """Spearman is None on a constant resample; that must not stop training (#169)."""
        by_gw = {1: [1.0], 2: [2.0], 3: [3.0]}
        out = train_gbm.bootstrap(
            by_gw, {"sometimes": lambda s: None if len(set(s)) == 1 else sum(s)}, draws=200
        )
        low, high = out["sometimes"]
        self.assertLessEqual(low, high)

    def test_never_a_value_is_no_interval(self):
        out = train_gbm.bootstrap({1: [1.0]}, {"never": lambda s: None}, draws=10)
        self.assertEqual(out["never"], (None, None))


class TestNaive(unittest.TestCase):
    def test_recent_rate_times_recent_minutes(self):
        row = {"points_per90_5": 6.0, "minutes_mean5": 45.0, "n_fixtures": 2}
        self.assertEqual(train_gbm.naive(row), 6.0)

    def test_no_form_is_zero(self):
        row = {"points_per90_5": None, "minutes_mean5": 90.0, "n_fixtures": 1}
        self.assertEqual(train_gbm.naive(row), 0.0)


if __name__ == "__main__":
    unittest.main()
