"""Tests for the feature-window logic in predict_baseline.

The first two classes here guard hard rule 2 — never use post-deadline
information to predict a gameweek. Everything else in the repo is recoverable;
a leak is not, because the track record it invalidates is the whole point of
the project.

Run: python -m unittest discover tests
"""

import unittest

from fpl.predict_baseline import (
    predict_player,
    recent_history,
    usable_rounds,
    weighted,
)


def row(rnd, minutes=90, points=5):
    return {"round": rnd, "minutes": minutes, "total_points": points}


class TestNoLeak(unittest.TestCase):
    """A row at or after the target gameweek must never survive."""

    def test_target_round_excluded(self):
        hist = [row(1), row(2), row(3)]
        out = recent_history(hist, target_gw=3, usable={1, 2, 3})
        self.assertEqual(len(out), 2, "GW3 must not feed a GW3 prediction")

    def test_future_rounds_excluded(self):
        hist = [row(1), row(2), row(5), row(9)]
        out = recent_history(hist, target_gw=2, usable={1, 2, 5, 9})
        self.assertEqual(len(out), 1)

    def test_target_bound_holds_even_if_usable_is_wrong(self):
        """usable making the target available must not defeat the bound.

        The redundant check in recent_history exists for exactly this: a bug
        upstream in usable_rounds should not become a silent leak.
        """
        hist = [row(4), row(3)]
        out = recent_history(hist, target_gw=4, usable={3, 4, 5})
        self.assertEqual(len(out), 1)

    def test_missing_round_is_dropped(self):
        out = recent_history([{"minutes": 90, "total_points": 5}], 3, {1, 2})
        self.assertEqual(out, [])


class TestDataChecked(unittest.TestCase):
    """Rounds without final results must not become features."""

    def test_unchecked_round_excluded(self):
        hist = [row(1), row(2), row(3)]
        out = recent_history(hist, target_gw=4, usable={1, 2})
        self.assertEqual(len(out), 2, "GW3 lacks data_checked and must be dropped")

    def test_usable_rounds_splits_on_data_checked(self):
        events = [
            {"id": 1, "data_checked": True},
            {"id": 2, "data_checked": True},
            {"id": 3, "data_checked": False},
            {"id": 4, "data_checked": False},
        ]
        usable, excluded = usable_rounds(events, target_gw=4)
        self.assertEqual(usable, {1, 2})
        self.assertEqual(excluded, [3])

    def test_usable_rounds_ignores_target_and_beyond(self):
        events = [{"id": i, "data_checked": True} for i in range(1, 6)]
        usable, excluded = usable_rounds(events, target_gw=3)
        self.assertEqual(usable, {1, 2})
        self.assertEqual(excluded, [])


class TestDoubles(unittest.TestCase):
    """Doubles sum points but must not inflate minutes."""

    def test_points_summed_within_round(self):
        hist = [row(1, 90, 5), row(1, 90, 7)]
        out = recent_history(hist, target_gw=2, usable={1})
        self.assertEqual(out[0]["points"], 12)

    def test_fixture_count_recorded(self):
        hist = [row(1, 90, 5), row(1, 90, 7)]
        out = recent_history(hist, target_gw=2, usable={1})
        self.assertEqual(out[0]["fixtures"], 2)
        self.assertEqual(out[0]["minutes"], 180)

    def test_rotation_player_minutes_not_inflated(self):
        """The bug this guards: a double must not raise per-fixture minutes."""
        hist = [row(1, 45, 2), row(2, 45, 2), row(2, 45, 2)]
        out = recent_history(hist, target_gw=3, usable={1, 2})
        per_fixture = [r["minutes"] / r["fixtures"] for r in out]
        self.assertEqual(per_fixture, [45.0, 45.0])
        self.assertAlmostEqual(weighted(per_fixture)[0], 45.0)


class TestPredictPlayer(unittest.TestCase):
    """End-to-end on one player, so the per-fixture fix is actually exercised."""

    AVAILABLE = {"status": "a", "chance_of_playing_next_round": None}

    def test_double_does_not_inflate_expected_minutes(self):
        hist = [row(1, 45, 2), row(2, 45, 2), row(2, 45, 2)]
        _, exp_minutes, _ = predict_player(
            self.AVAILABLE, hist, "MID", n_fixtures=1, target_gw=3, usable={1, 2}
        )
        self.assertAlmostEqual(exp_minutes, 45.0, places=1)

    def test_blank_gameweek_is_zero(self):
        hist = [row(1), row(2)]
        pred, mins, pp90 = predict_player(
            self.AVAILABLE, hist, "MID", n_fixtures=0, target_gw=3, usable={1, 2}
        )
        self.assertEqual((pred, mins, pp90), (0.0, 0.0, 0.0))

    def test_unavailable_player_scores_zero(self):
        hist = [row(1), row(2)]
        pred, mins, _ = predict_player(
            {"status": "u"}, hist, "MID", n_fixtures=1, target_gw=3, usable={1, 2}
        )
        self.assertEqual(pred, 0.0)
        self.assertEqual(mins, 0.0)

    def test_no_usable_history_scores_zero(self):
        hist = [row(1), row(2)]
        pred, _, _ = predict_player(
            self.AVAILABLE, hist, "MID", n_fixtures=1, target_gw=3, usable=set()
        )
        self.assertEqual(pred, 0.0)


class TestOrdering(unittest.TestCase):
    def test_most_recent_first(self):
        out = recent_history([row(1), row(3), row(2)], target_gw=4, usable={1, 2, 3})
        self.assertEqual([r["points"] for r in out], [5, 5, 5])
        self.assertEqual(len(out), 3)

    def test_lookback_window_is_bounded(self):
        hist = [row(i) for i in range(1, 12)]
        out = recent_history(hist, target_gw=12, usable=set(range(1, 12)))
        self.assertEqual(len(out), 5, "LOOKBACK caps the window at 5 rounds")


if __name__ == "__main__":
    unittest.main()
