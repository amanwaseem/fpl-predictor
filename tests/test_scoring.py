"""Tests for fpl/score.py, the scoring harness.

Every test builds two synthetic snapshots: the one an entry was predicted from
(target GW4) and a later one holding the actual results (target GW5, so GW4 is
settled). Predictions are integers so every expected metric can be worked out
by hand.

Run from the repository root: python -m unittest discover tests
"""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path

from fpl import score
from tests import fixtures

PREDICTED_AT = "20260910T000000Z"
ACTUALS_AT = "20260920T000000Z"


class ScoringCase(fixtures.TempCwd):
    """A league, a GW4 entry with integer predictions, and GW4 actuals."""

    def setUp(self):
        super().setUp()
        self.pred_snap = self.league(PREDICTED_AT, target_gw=4, latest=False)
        bootstrap = json.loads((self.pred_snap / "bootstrap.json").read_text())
        fixtures_ = json.loads((self.pred_snap / "fixtures.json").read_text())
        self.rows = fixtures.entry_rows(bootstrap, fixtures_, 4,
                                        "2026-09-12T12:30:00Z",
                                        snapshot_id=PREDICTED_AT)
        for row in self.rows:
            row["predicted_points"] = float(row["player_id"] % 5)
        self.rows.sort(key=lambda r: (-r["predicted_points"], r["player_id"]))
        self.ids = [r["player_id"] for r in self.rows]

        self.actual_snap = self.league(ACTUALS_AT, target_gw=5, latest=True)
        # Everyone scores exactly what was predicted, except player 1 (+8).
        self.results = {pid: (pid % 5 + (8 if pid == 1 else 0), 90) for pid in self.ids}

    def write_entry(self, rows=None, name="gw04_baseline-v1.csv"):
        return fixtures.write_csv(Path("predictions") / name,
                                  self.rows if rows is None else rows)

    def run_harness(self, actuals=None):
        fixtures.live(self.actual_snap, 4, self.results)
        with contextlib.redirect_stdout(io.StringIO()):
            return score.main(actuals, "scores")

    def metrics(self, name="gw04_baseline-v1"):
        return json.loads(Path(f"scores/{name}.json").read_text())


class TestGolden(ScoringCase):

    def test_hand_computed_metrics(self):
        """80 players, one error of 8: MAE 0.1, RMSE sqrt(0.8), bias -0.1."""
        self.write_entry()
        self.run_harness()
        m = self.metrics()["metrics"]["all"]
        self.assertEqual(m["n"], 80)
        self.assertAlmostEqual(m["mae"], 0.1)
        self.assertAlmostEqual(m["rmse"], round(0.8 ** 0.5, 4))
        self.assertAlmostEqual(m["bias"], -0.1)

    def test_output_files(self):
        self.write_entry()
        self.run_harness()
        names = sorted(p.name for p in Path("scores").iterdir())
        self.assertEqual(names, ["cumulative.json", "gw04_baseline-v1.csv",
                                 "gw04_baseline-v1.json"])
        rows = fixtures.read_csv("scores/gw04_baseline-v1.csv")
        self.assertEqual(len(rows), 80)
        self.assertEqual(list(rows[0]), score.SCORE_FIELDS)
        one = next(r for r in rows if r["player_id"] == "1")
        self.assertEqual((one["predicted_points"], one["actual_points"], one["error"]),
                         ("1.0", "9", "-8.0"))

    def test_provenance_stamped(self):
        self.write_entry()
        self.run_harness()
        m = self.metrics()
        self.assertEqual(m["harness_version"], score.HARNESS_VERSION)
        self.assertEqual(m["prediction_snapshot_id"], PREDICTED_AT)
        self.assertEqual(m["actuals_snapshot_id"], ACTUALS_AT)

    def test_restricted_to_predicted_minutes(self):
        """Players given zero expected minutes drop out of predicted_to_play."""
        for row in self.rows[:10]:
            row["expected_minutes"] = 0.0
        self.write_entry()
        self.run_harness()
        m = self.metrics()["metrics"]
        self.assertEqual(m["all"]["n"], 80)
        self.assertEqual(m["predicted_to_play"]["n"], 70)


class TestSettlement(ScoringCase):

    def test_unsettled_gameweek_refused(self):
        events = fixtures.season(5, fixtures.future_deadline(), checked_through=3)
        matched_rows = self.rows
        with self.assertRaises(SystemExit):
            score.score_entry(matched_rows, 4, "baseline-v1", events,
                              {pid: {"points": 0, "minutes": 0} for pid in self.ids},
                              None)

    def test_unsettled_entry_is_pending_not_scored(self):
        # Replace the actuals snapshot with one where GW4 is still provisional.
        self.actual_snap = self.league(
            "20260921T000000Z", target_gw=5, latest=True,
            events=fixtures.season(5, fixtures.future_deadline(), checked_through=3))
        self.write_entry()
        self.run_harness()
        self.assertFalse(Path("scores/gw04_baseline-v1.json").exists())
        cumulative = json.loads(Path("scores/cumulative.json").read_text())
        self.assertEqual(cumulative["models"]["baseline-v1"]["gameweeks_pending"], [4])

    def test_empty_log_refused(self):
        Path("predictions").mkdir()
        with self.assertRaises(SystemExit):
            self.run_harness()


class TestJoin(ScoringCase):

    def test_log_only_player_is_bucketed_not_zeroed(self):
        del self.results[self.ids[0]]
        self.write_entry()
        self.run_harness()
        m = self.metrics()
        self.assertEqual(m["join"]["missing_actual"],
                         {"count": 1, "player_ids": [self.ids[0]]})
        self.assertEqual(m["metrics"]["all"]["n"], 79)

    def test_actuals_only_player_is_bucketed(self):
        self.results[999] = (12, 90)
        self.write_entry()
        self.run_harness()
        m = self.metrics()
        self.assertEqual(m["join"]["missing_prediction"],
                         {"count": 1, "player_ids": [999]})
        self.assertEqual(m["metrics"]["all"]["n"], 80)
        self.assertLess(m["metrics"]["all"]["mae"], 1, "999's 12 points were not scored")


class TestComparisons(ScoringCase):

    def test_comparators_read_the_prediction_snapshot(self):
        self.write_entry()
        self.run_harness()
        comp = self.metrics()["comparators"]
        self.assertTrue(comp["available"])
        self.assertIsNone(comp["zero"]["spearman"], "a constant predictor has no ranking")
        self.assertEqual(set(comp), {"available", "zero", "points_per_game", "form"})

    def test_comparators_unavailable_without_the_snapshot(self):
        """data/raw is not shared; a missing prediction snapshot is not fatal."""
        self.write_entry()
        (self.pred_snap / "bootstrap.json").unlink()
        self.run_harness()
        self.assertFalse(self.metrics()["comparators"]["available"])

    def test_comparators_carried_forward_on_a_fresh_clone(self):
        """A rerun without the prediction snapshot must not erase what it had."""
        self.write_entry()
        self.run_harness()
        before = Path("scores/gw04_baseline-v1.json").read_bytes()
        (self.pred_snap / "bootstrap.json").unlink()
        self.run_harness()
        self.assertEqual(Path("scores/gw04_baseline-v1.json").read_bytes(), before)
        self.assertTrue(self.metrics()["comparators"]["available"])

    def test_comparators_not_carried_across_harness_versions(self):
        """A carried block must mean what a recomputed one would."""
        self.write_entry()
        self.run_harness()
        path = Path("scores/gw04_baseline-v1.json")
        stale = json.loads(path.read_text())
        stale["harness_version"] = "harness-v0"
        path.write_text(json.dumps(stale))
        (self.pred_snap / "bootstrap.json").unlink()
        self.run_harness()
        self.assertFalse(self.metrics()["comparators"]["available"])

    def test_baseline_has_no_head_to_head(self):
        self.write_entry()
        self.run_harness()
        self.assertFalse(self.metrics()["head_to_head"]["applicable"])

    def test_second_model_meets_the_baseline(self):
        self.write_entry()
        better = [dict(r, model_version="challenger-v1") for r in self.rows]
        self.write_entry(better, "gw04_challenger-v1.csv")
        self.run_harness()
        h2h = self.metrics("gw04_challenger-v1")["head_to_head"]
        self.assertTrue(h2h["applicable"])
        self.assertEqual(h2h["n"], 80)
        self.assertAlmostEqual(h2h["model"]["mae"], h2h["baseline"]["mae"])

    def test_xi_optimum_bounds_captured(self):
        self.write_entry()
        self.run_harness()
        xi = self.metrics()["xi"]
        self.assertLessEqual(xi["captured"]["actual"], xi["optimum"]["actual"])
        self.assertEqual(len(xi["captured"]["player_ids"]), 11)


class TestCumulative(ScoringCase):

    def test_missed_gameweek_is_listed(self):
        """Entries for GW2 and GW4 with GW3 settled: GW3 is missed, not skipped."""
        self.write_entry()
        gw2 = [dict(r, gameweek=2) for r in self.rows]
        self.write_entry(gw2, "gw02_baseline-v1.csv")
        fixtures.live(self.actual_snap, 2, self.results)
        self.run_harness()
        block = json.loads(Path("scores/cumulative.json").read_text())["models"]["baseline-v1"]
        self.assertEqual(block["gameweeks_scored"], [2, 4])
        self.assertEqual(block["gameweeks_missed"], [3])

    def test_pooled_over_rows(self):
        self.write_entry()
        gw2 = [dict(r, gameweek=2) for r in self.rows]
        self.write_entry(gw2, "gw02_baseline-v1.csv")
        fixtures.live(self.actual_snap, 2, {pid: (pid % 5, 90) for pid in self.ids})
        self.run_harness()
        pooled = json.loads(Path("scores/cumulative.json").read_text())[
            "models"]["baseline-v1"]["pooled"]["all"]
        # GW2 perfect, GW4 one error of 8: 8 over 160 rows.
        self.assertEqual(pooled["n"], 160)
        self.assertAlmostEqual(pooled["mae"], 0.05)


class TestLogUntouched(ScoringCase):

    def digest(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(Path("predictions").iterdir())}

    def test_predictions_are_byte_identical_after_a_run(self):
        self.write_entry()
        before = self.digest()
        self.run_harness()
        self.assertEqual(self.digest(), before)

    def test_output_into_predictions_refused(self):
        self.write_entry()
        fixtures.live(self.actual_snap, 4, self.results)
        with self.assertRaises(SystemExit):
            score.main(None, "predictions")
        with self.assertRaises(SystemExit):
            score.main(None, "predictions/scores")

    def test_repo_under_a_folder_named_predictions(self):
        """~/work/predictions/fpl-predictor must still be able to write scores/."""
        clone = self.tmp / "work" / "predictions" / "fpl-predictor"
        (clone / "data").mkdir(parents=True)
        (self.tmp / "data/raw").rename(clone / "data/raw")
        os.chdir(clone)
        self.pred_snap = clone / "data/raw" / PREDICTED_AT
        self.actual_snap = clone / "data/raw" / ACTUALS_AT
        self.write_entry()
        self.assertEqual(self.run_harness(), 0)
        self.assertTrue(Path("scores/gw04_baseline-v1.json").exists())

    def test_idempotent(self):
        self.write_entry()
        self.run_harness()
        first = {p.name: p.read_bytes() for p in Path("scores").iterdir()}
        self.run_harness()
        second = {p.name: p.read_bytes() for p in Path("scores").iterdir()}
        self.assertEqual(first, second)

    def test_malformed_entry_name_refused(self):
        self.write_entry(name="gw4_baseline-v1.csv")
        with self.assertRaises(SystemExit):
            self.run_harness()
