"""gbm-v1's prediction side, fpl.models.gbm (#85): no LightGBM needed."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fpl.models import gbm
from tests.gbm_fixture import tiny_artifact, tiny_bundle


def feature_row(**values):
    row = dict.fromkeys(gbm.FEATURES)
    row |= {"position": "MID", "n_fixtures": 1, "gameweek": 5}
    return row | values


class TestVector(unittest.TestCase):
    def test_inputs_follow_the_trained_order_with_position_as_indicators(self):
        x = gbm.vector(feature_row(position="DEF", price=55))
        self.assertEqual(len(x), len(gbm.FEATURES))
        self.assertEqual(x[gbm.FEATURES.index("price")], 55.0)
        self.assertEqual(x[gbm.FEATURES.index("is_def")], 1.0)
        self.assertEqual(x[gbm.FEATURES.index("is_mid")], 0.0)
        self.assertIsNone(x[gbm.FEATURES.index("xg_per90_5")])

    def test_an_artifact_built_on_another_feature_version_is_refused(self):
        with self.assertRaises(ValueError):
            gbm.Bundle(tiny_artifact() | {"feature_version": "features-v0"})

    def test_an_artifact_for_other_inputs_is_refused(self):
        with self.assertRaises(ValueError):
            gbm.Bundle({"features": gbm.FEATURES[:-1], "models": {}})


class TestBundle(unittest.TestCase):
    def test_minutes_are_at_most_90_a_fixture(self):
        bundle = tiny_bundle()
        _, minutes = bundle.predict(feature_row(minutes_mean5=90.0, n_fixtures=1))
        self.assertLessEqual(minutes, 90.0)
        _, double = bundle.predict(feature_row(minutes_mean5=90.0, n_fixtures=2))
        self.assertGreater(double, 0.0)

    def test_better_form_predicts_more_points(self):
        bundle = tiny_bundle()
        low, _ = bundle.predict(feature_row(points_per90_5=2.0))
        high, _ = bundle.predict(feature_row(points_per90_5=6.0))
        self.assertGreater(high, low)


def snapshot(status="a", chance=None):
    bootstrap = {
        "elements": [
            {
                "id": 1,
                "web_name": "a",
                "element_type": 3,
                "team": 1,
                "now_cost": 60,
                "status": status,
                "chance_of_playing_next_round": chance,
            },
            {
                "id": 2,
                "web_name": "b",
                "element_type": 2,
                "team": 3,
                "now_cost": 45,
                "status": "a",
                "chance_of_playing_next_round": None,
            },
        ],
        "teams": [{"id": t, "short_name": f"T{t}"} for t in (1, 2, 3)],
        "element_types": [
            {"id": 2, "singular_name_short": "DEF"},
            {"id": 3, "singular_name_short": "MID"},
        ],
        "events": [
            {
                "id": gw,
                "deadline_time": f"2026-08-{14 + 7 * (gw - 1):02d}T17:30:00Z",
                "finished": gw < 3,
                "data_checked": gw < 3,
            }
            for gw in (1, 2, 3)
        ],
    }
    fixtures = [
        {
            "id": 1,
            "event": 1,
            "team_h": 1,
            "team_a": 2,
            "team_h_score": 1,
            "team_a_score": 0,
            "finished": True,
        },
        {
            "id": 2,
            "event": 2,
            "team_h": 2,
            "team_a": 1,
            "team_h_score": 2,
            "team_a_score": 2,
            "finished": True,
        },
        {
            "id": 3,
            "event": 3,
            "team_h": 1,
            "team_a": 2,
            "team_h_score": None,
            "team_a_score": None,
            "finished": False,
        },
    ]
    histories = {
        1: [
            {
                "round": gw,
                "fixture": gw,
                "was_home": gw == 1,
                "minutes": 90,
                "total_points": 8,
                "bonus": 1,
                "value": 60,
                "expected_goals": "0.5",
                "expected_assists": "0.2",
            }
            for gw in (1, 2)
        ],
        2: [],
    }
    pasts = {
        1: [{"season_name": "2025/26", "element_code": 77, "minutes": 3000, "total_points": 150}]
    }
    return bootstrap, fixtures, histories, pasts


class TestPredictRows(unittest.TestCase):
    def rows(self, **kwargs):
        bootstrap, fixtures, histories, pasts = snapshot(**kwargs)
        return {
            r["player_id"]: r
            for r in gbm.predict_rows(
                bootstrap, fixtures, histories, pasts, 3, {1, 2}, tiny_bundle()
            )
        }

    def test_every_player_with_a_history_has_a_row(self):
        self.assertEqual(sorted(self.rows()), [1, 2])

    def test_a_player_whose_club_does_not_play_is_predicted_zero(self):
        row = self.rows()[2]
        self.assertEqual(
            (row["predicted_points"], row["expected_minutes"], row["n_fixtures"]), (0.0, 0.0, 0)
        )

    def test_an_unavailable_player_is_zeroed(self):
        row = self.rows(status="i")[1]
        self.assertEqual((row["predicted_points"], row["expected_minutes"]), (0.0, 0.0))

    def test_a_doubtful_player_is_scaled_by_his_chance(self):
        full, half = self.rows()[1], self.rows(status="d", chance=50)[1]
        self.assertAlmostEqual(half["expected_minutes"], full["expected_minutes"] / 2, delta=0.1)
        self.assertLessEqual(half["expected_minutes"], 45.0)

    def test_last_season_is_read_from_history_past(self):
        bootstrap, fixtures, histories, pasts = snapshot()
        data = gbm.season_data(bootstrap, fixtures, histories, pasts, {1, 2})
        self.assertEqual(data.players[1]["code"], 77)
        self.assertEqual(data.previous[77], {"minutes": 3000, "points": 150})
        self.assertEqual(data.players[2]["code"], -2)


class TestLoad(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.body = json.dumps(tiny_artifact()).encode()
        self.registry = self.dir / "registry.json"

    def register(self, digest):
        self.registry.write_text(
            json.dumps({"gbm-v1": {"sha256": digest, "url": "https://example.invalid/gbm-v1.json"}})
        )

    def test_the_artifact_is_fetched_once_and_checked(self):
        self.register(hashlib.sha256(self.body).hexdigest())
        response = mock.Mock(content=self.body, raise_for_status=mock.Mock())
        with mock.patch.object(gbm.requests, "get", return_value=response) as get:
            gbm.load(registry=self.registry, cache=self.dir / "cache")
            gbm.load(registry=self.registry, cache=self.dir / "cache")
        self.assertEqual(get.call_count, 1)

    def test_an_artifact_whose_hash_differs_is_refused_and_not_cached(self):
        self.register("0" * 64)
        response = mock.Mock(content=self.body, raise_for_status=mock.Mock())
        with (
            mock.patch.object(gbm.requests, "get", return_value=response),
            self.assertRaises(SystemExit),
        ):
            gbm.load(registry=self.registry, cache=self.dir / "cache")
        self.assertFalse((self.dir / "cache").exists())


if __name__ == "__main__":
    unittest.main()
