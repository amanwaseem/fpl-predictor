"""Tiny gbm bundles for tests: one split per model, in LightGBM's text format.

No LightGBM needed, and every model reads a form column, so a leak into form
would change its output (the backtest's poisoning test relies on that).
gbm-v2's points model reads a defensive-contribution column instead, so a leak
into those (#174) would change its output too.
"""

from fpl.models.feature_table import FEATURE_VERSION
from fpl.models.gbm import MODEL_VERSION, VERSIONS, Bundle


def model_text(objective, feature, threshold, low, high, features):
    return "\n".join(
        [
            "tree",
            "version=v4",
            "num_class=1",
            "num_tree_per_iteration=1",
            "label_index=0",
            f"max_feature_idx={len(features) - 1}",
            f"objective={objective}",
            f"feature_names={' '.join(features)}",
            "",
            "Tree=0",
            "num_leaves=2",
            "num_cat=0",
            f"split_feature={features.index(feature)}",
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


def tiny_models(version=MODEL_VERSION):
    features = VERSIONS[version]
    points = ("points_per90_5", 4.0) if version == "gbm-v1" else ("defensive_per90_5", 8.0)
    return {
        "sixty": model_text("binary sigmoid:1", "minutes_mean5", 45.0, -2.0, 2.0, features),
        "minutes_long": model_text("regression", "minutes_mean5", 80.0, 75.0, 88.0, features),
        "minutes_short": model_text("regression", "minutes_mean5", 10.0, 0.0, 20.0, features),
        "points": model_text("tweedie", *points, 0.2, 1.4, features),
    }


def tiny_artifact(version=MODEL_VERSION):
    return {
        "version": version,
        "features": VERSIONS[version],
        "feature_version": FEATURE_VERSION,
        "models": tiny_models(version),
    }


def tiny_bundle(version=MODEL_VERSION):
    return Bundle(tiny_artifact(version))
