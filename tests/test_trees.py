"""fpl.models.trees against LightGBM itself (#85).

The plain-Python evaluator must give LightGBM's own prediction for every row,
missing values included, for each objective gbm-v1 uses. Needs the `ml` group;
skips without it.
"""

import math
import random
import unittest

from fpl.models import trees

try:
    import lightgbm
    import numpy
# OSError: installed, but its OpenMP runtime is not (brew install libomp on macOS).
except (ImportError, OSError):  # pragma: no cover - the ml group is not usable
    lightgbm = None


def data(n=600, features=6, seed=3):
    rng = random.Random(seed)
    rows, ys = [], []
    for _ in range(n):
        row = [rng.gauss(0, 1) if rng.random() > 0.15 else math.nan for _ in range(features)]
        row[2] = 0.0 if rng.random() < 0.3 else row[2]  # zeros, for the zero-missing rule
        signal = sum(v for v in row[:3] if not math.isnan(v))
        ys.append(signal)
        rows.append(row)
    return rows, ys


@unittest.skipIf(lightgbm is None, "LightGBM is in the ml group: uv sync")
class TestAgainstLightGBM(unittest.TestCase):
    def assert_same(self, objective, label, **params):
        rows, signal = data()
        y = [label(s) for s in signal]
        booster = lightgbm.train(
            {
                "objective": objective,
                "num_leaves": 15,
                "min_data_in_leaf": 5,
                "verbose": -1,
                "deterministic": True,
                "num_threads": 1,
                "seed": 1,
                **params,
            },
            lightgbm.Dataset(numpy.array(rows), label=numpy.array(y)),
            num_boost_round=40,
        )
        model = trees.Model(booster.model_to_string())
        expected = booster.predict(numpy.array(rows))
        for row, want in zip(rows, expected, strict=True):
            self.assertAlmostEqual(model.predict(row), want, places=12)

    def test_regression(self):
        self.assert_same("regression", lambda s: s)

    def test_binary(self):
        self.assert_same("binary", lambda s: 1.0 if s > 0 else 0.0)

    def test_tweedie(self):
        self.assert_same("tweedie", lambda s: max(s, 0.0) * 2, tweedie_variance_power=1.4)

    def test_zero_as_missing(self):
        self.assert_same("regression", lambda s: s, zero_as_missing=True)

    def test_none_is_missing_like_nan(self):
        rows, signal = data()
        booster = lightgbm.train(
            {"objective": "regression", "verbose": -1, "min_data_in_leaf": 5},
            lightgbm.Dataset(numpy.array(rows), label=numpy.array(signal)),
            num_boost_round=10,
        )
        model = trees.Model(booster.model_to_string())
        row = rows[0][:]
        row[0] = None
        nan_row = rows[0][:]
        nan_row[0] = math.nan
        self.assertEqual(model.predict(row), model.predict(nan_row))


class TestRefusals(unittest.TestCase):
    def test_an_unsupported_objective_is_refused_on_reading(self):
        text = "\n".join(
            [
                "tree",
                "num_class=1",
                "objective=lambdarank",
                "feature_names=a",
                "",
                "Tree=0",
                "num_leaves=1",
                "leaf_value=0",
                "",
            ]
        )
        with self.assertRaises(ValueError):
            trees.Model(text)

    def test_a_one_leaf_tree_is_its_leaf(self):
        text = "\n".join(
            [
                "tree",
                "num_class=1",
                "objective=regression",
                "feature_names=a",
                "",
                "Tree=0",
                "num_leaves=1",
                "leaf_value=0.25",
                "",
                "end of trees",
                "",
            ]
        )
        self.assertEqual(trees.Model(text).predict([1.0]), 0.25)


if __name__ == "__main__":
    unittest.main()
