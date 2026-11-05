"""A tiny gbm-v1 bundle for tests: one split per model, in LightGBM's text format.

No LightGBM needed, and every model reads a form column, so a leak into form
would change its output (the backtest's poisoning test relies on that).
"""

from fpl.models.feature_table import FEATURE_VERSION
from fpl.models.gbm import FEATURES, Bundle


def model_text(objective, feature, threshold, low, high):
    return "\n".join(
        [
            "tree",
            "version=v4",
            "num_class=1",
            "num_tree_per_iteration=1",
            "label_index=0",
            f"max_feature_idx={len(FEATURES) - 1}",
            f"objective={objective}",
            f"feature_names={' '.join(FEATURES)}",
            "",
            "Tree=0",
            "num_leaves=2",
            "num_cat=0",
            f"split_feature={FEATURES.index(feature)}",
            f"threshold={threshold}",
            "decision_type=2",
            "left_child=-1",
            "right_child=-2",
            f"leaf_value={low} {high}",
            "",
            "end of trees",
            "",
        ]
    )


def tiny_models():
    return {
        "sixty": model_text("binary sigmoid:1", "minutes_mean5", 45.0, -2.0, 2.0),
        "minutes_long": model_text("regression", "minutes_mean5", 80.0, 75.0, 88.0),
        "minutes_short": model_text("regression", "minutes_mean5", 10.0, 0.0, 20.0),
        "points": model_text("tweedie", "points_per90_5", 4.0, 0.2, 1.4),
    }


def tiny_artifact():
    return {
        "version": "gbm-v1",
        "features": FEATURES,
        "feature_version": FEATURE_VERSION,
        "models": tiny_models(),
    }


def tiny_bundle():
    return Bundle(tiny_artifact())
