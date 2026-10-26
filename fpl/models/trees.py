"""LightGBM models evaluated in plain Python, from their text format (#85).

A logged prediction runs with `requests` alone (ADR 0017), so the model that
makes gbm-v1's entries cannot need LightGBM, numpy or a compiler: training
does, in the `ml` group, and saves each model with `model_to_string()`. This
reads that text and walks its trees, following LightGBM's own rules for a
missing value and for which side of a split it goes, so the two agree to the
last bit (tests/test_trees.py checks it against LightGBM itself).

Only what gbm-v1 uses is supported: numerical splits, one output, and the
regression, binary and Tweedie objectives. Anything else is refused when the
model is read, not mispredicted later.
"""

import math
from dataclasses import dataclass

# decision_type bits (LightGBM's tree.h).
CATEGORICAL = 1
DEFAULT_LEFT = 2
MISSING_NONE, MISSING_ZERO, MISSING_NAN = 0, 1, 2
ZERO_THRESHOLD = 1e-35


@dataclass
class Tree:
    split_feature: list[int]
    threshold: list[float]
    decision_type: list[int]
    left_child: list[int]
    right_child: list[int]
    leaf_value: list[float]

    def value(self, row):
        if not self.split_feature:
            return self.leaf_value[0]
        node = 0
        while node >= 0:
            x = row[self.split_feature[node]]
            decision = self.decision_type[node]
            missing = (decision >> 2) & 3
            if x is None or (isinstance(x, float) and math.isnan(x)):
                x = math.nan if missing == MISSING_NAN else 0.0
            if (missing == MISSING_ZERO and abs(x) < ZERO_THRESHOLD) or (
                missing == MISSING_NAN and math.isnan(x)
            ):
                go_left = bool(decision & DEFAULT_LEFT)
            else:
                go_left = x <= self.threshold[node]
            node = self.left_child[node] if go_left else self.right_child[node]
        return self.leaf_value[~node]


class Model:
    """A LightGBM model read from model_to_string()."""

    def __init__(self, text):
        header, *blocks = text.split("\nTree=")
        fields = dict(line.split("=", 1) for line in header.splitlines() if "=" in line)
        if fields.get("num_class", "1") != "1" or fields.get("num_tree_per_iteration", "1") != "1":
            raise ValueError("only single-output models are supported")
        self.feature_names = fields["feature_names"].split()
        objective = fields["objective"].split()
        self.objective = objective[0]
        if self.objective not in ("regression", "binary", "tweedie"):
            raise ValueError(f"objective {self.objective!r} is not supported")
        # Options are name:value; a bare word (regression's "sqrt") changes the
        # output's scale, which this does not undo.
        bare = [o for o in objective[1:] if ":" not in o]
        if bare:
            raise ValueError(f"objective option(s) {bare} are not supported")
        options = dict(o.split(":", 1) for o in objective[1:])
        self.sigmoid = float(options.get("sigmoid", 1.0))
        if "average_output" in header.split():
            raise ValueError("averaged output (random forest) is not supported")
        self.trees = [self._tree(block) for block in blocks]

    @staticmethod
    def _tree(block):
        block = block.split("end of trees", 1)[0]
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if int(fields.get("num_cat", "0")):
            raise ValueError("categorical splits are not supported")
        if int(fields.get("is_linear", "0")):
            raise ValueError("linear trees are not supported")

        def numbers(key, kind):
            return [kind(v) for v in fields[key].split()] if fields.get(key) else []

        tree = Tree(
            split_feature=numbers("split_feature", int),
            threshold=numbers("threshold", float),
            decision_type=numbers("decision_type", int),
            left_child=numbers("left_child", int),
            right_child=numbers("right_child", int),
            leaf_value=numbers("leaf_value", float),
        )
        if any(d & CATEGORICAL for d in tree.decision_type):
            raise ValueError("categorical splits are not supported")
        return tree

    def raw(self, row):
        """The summed tree outputs for one row, a list in feature_names order."""
        return sum(tree.value(row) for tree in self.trees)

    def predict(self, row):
        """The prediction for one row, on the objective's scale."""
        score = self.raw(row)
        if self.objective == "binary":
            return 1.0 / (1.0 + math.exp(-self.sigmoid * score))
        if self.objective == "tweedie":
            return math.exp(score)
        return score
