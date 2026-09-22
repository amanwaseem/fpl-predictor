"""Tests for fpl/metrics.py against hand-computed values.

Every expected number here was worked out by hand, not by running the code, so
a test that passes is agreement between two independent derivations.

Run from the repository root: python -m unittest discover tests
"""

import math
import unittest

from fpl.metrics import (
    average_ranks,
    bias,
    mae,
    pooled_summary,
    rmse,
    spearman,
    summary,
)

# Errors of +1, -2 and 0.
PAIRS = [(1.0, 0), (2.0, 4), (3.0, 3)]


class TestErrors(unittest.TestCase):

    def test_mae(self):
        self.assertAlmostEqual(mae(PAIRS), 1.0)

    def test_rmse(self):
        self.assertAlmostEqual(rmse(PAIRS), math.sqrt(5 / 3))

    def test_bias_sign(self):
        """Positive bias means overprediction; this set underpredicts."""
        self.assertAlmostEqual(bias(PAIRS), -1 / 3)

    def test_empty_refused(self):
        for metric in (mae, rmse, bias, summary):
            with self.assertRaises(ValueError):
                metric([])


class TestAverageRanks(unittest.TestCase):

    def test_distinct(self):
        self.assertEqual(average_ranks([30, 10, 20]), [3, 1, 2])

    def test_ties_share_the_mean_rank(self):
        # Three zeros span ranks 1-3 and each take 2.
        self.assertEqual(average_ranks([0, 5, 0, 0]), [2, 4, 2, 2])

    def test_heavily_tied(self):
        """The shape of a real entry: most players tied at zero."""
        values = [0.0] * 8 + [1.5, 3.0]
        self.assertEqual(average_ranks(values), [4.5] * 8 + [9, 10])


class TestSpearman(unittest.TestCase):

    def test_known_value_with_a_tie(self):
        # Actual ranks 1, 2, 3.5, 5, 3.5. Against predicted ranks 1..5:
        # cov = 8, sum sq = 10 and 9.5, so rho = 8 / sqrt(95).
        rho = spearman([1, 2, 3, 4, 5], [5, 6, 7, 8, 7])
        self.assertAlmostEqual(rho, 8 / math.sqrt(95))

    def test_perfect_and_reversed(self):
        self.assertAlmostEqual(spearman([1, 2, 3], [10, 20, 30]), 1.0)
        self.assertAlmostEqual(spearman([1, 2, 3], [30, 20, 10]), -1.0)

    def test_monotone_transform_is_invisible(self):
        """Rank correlation cares about order, not scale."""
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [1, 4, 9, 16]), 1.0)

    def test_constant_predictor_is_undefined_not_zero(self):
        """The all-zero comparator has no ordering. None, not 0, not a crash."""
        self.assertIsNone(spearman([0, 0, 0, 0], [1, 5, 2, 0]))

    def test_constant_actuals_is_undefined(self):
        self.assertIsNone(spearman([1, 2, 3], [0, 0, 0]))

    def test_too_few_values(self):
        self.assertIsNone(spearman([1], [1]))
        self.assertIsNone(spearman([], []))

    def test_length_mismatch_refused(self):
        with self.assertRaises(ValueError):
            spearman([1, 2], [1])


class TestSummary(unittest.TestCase):

    def test_fields(self):
        s = summary(PAIRS)
        self.assertEqual(s["n"], 3)
        self.assertAlmostEqual(s["mae"], 1.0)
        self.assertEqual(set(s), {"n", "mae", "rmse", "bias", "spearman"})

    def test_pooled_is_not_a_mean_of_means(self):
        """SPEC section 6. One bad row in a small gameweek must not count triple.

        GW A: one player, error 4. GW B: three players, error 0.
        Pooled MAE is 4/4 = 1.0; the mean of the two gameweek MAEs would be 2.0.
        """
        gw_a = [(4.0, 0)]
        gw_b = [(2.0, 2), (3.0, 3), (1.0, 1)]
        pooled = pooled_summary([gw_a, gw_b])
        self.assertEqual(pooled["n"], 4)
        self.assertAlmostEqual(pooled["mae"], 1.0)
        mean_of_means = (mae(gw_a) + mae(gw_b)) / 2
        self.assertNotAlmostEqual(pooled["mae"], mean_of_means)


if __name__ == "__main__":
    unittest.main()
