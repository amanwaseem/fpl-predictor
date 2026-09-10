"""A full baseline run against a synthetic snapshot, with no network.

The one test that exercises predict_baseline.main rather than its parts, and
the only place the predictor and the verifier are checked against each other.
Everything else in the suite tests a piece: this is the smoke test the GW4
runbook actually leans on, because the runbook is "run the predictor, then run
the verifier" and nothing else proves those two agree about the contract.

Run from the repository root: python -m unittest discover tests
"""

import csv
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tests import fixtures
from fpl import predict_baseline, verify_entry
from fpl.log import FIELDS

SNAPSHOT_ID = "20260910T000000Z"
TARGET_GW = 4
ENTRY = Path("predictions/gw04_baseline-v1.csv")


class TestBaselineEndToEnd(fixtures.TempCwd):
    def setUp(self):
        super().setUp()
        self.deadline = fixtures.future_deadline()
        self.dir = self.league(SNAPSHOT_ID, target_gw=TARGET_GW,
                               deadline=self.deadline)

    def predict(self, gw=TARGET_GW, out="predictions"):
        out_stream = io.StringIO()
        with redirect_stdout(out_stream):
            predict_baseline.main(gw, out)
        return out_stream.getvalue()

    def verify(self, path=ENTRY):
        out_stream = io.StringIO()
        with redirect_stdout(out_stream):
            code = verify_entry.main(path)
        return code, out_stream.getvalue()

    def test_baseline_run_produces_an_entry_the_verifier_accepts(self):
        self.predict()
        self.assertTrue(ENTRY.exists())
        code, output = self.verify()
        self.assertEqual(code, 0, f"verifier refused the baseline's own entry:\n{output}")

    def test_entry_covers_every_player_in_the_snapshot(self):
        self.predict()
        with ENTRY.open(newline="") as f:
            rows = list(csv.DictReader(f))
        bootstrap = json.loads((self.dir / "bootstrap.json").read_text())
        self.assertEqual(len(rows), len(bootstrap["elements"]))
        self.assertEqual(list(rows[0]), FIELDS)

    def test_run_reports_which_rounds_it_used(self):
        """The two lines the runbook says to read before trusting the output."""
        output = self.predict()
        self.assertIn("included: 1, 2, 3", output)
        self.assertIn("excluded: none", output)

    def test_provisional_round_is_excluded_and_reported(self):
        """A snapshot taken mid-gameweek must drop the unsettled round."""
        # A second snapshot, taken while GW3 is still being played. Writing it
        # with latest=True repoints data/raw/LATEST, so the predictor picks it
        # up in place of the settled one setUp built.
        self.league("20260911T000000Z", target_gw=TARGET_GW,
                    deadline=self.deadline,
                    events=fixtures.season(TARGET_GW, self.deadline,
                                           checked_through=2))
        output = self.predict()
        self.assertIn("included: 1, 2", output)
        self.assertIn("excluded: 3", output)

    def test_second_write_to_the_log_is_refused(self):
        """Hard rule 1, exercised through the real entry point."""
        self.predict()
        with self.assertRaises(SystemExit) as cm:
            self.predict()
        self.assertIn("already exists", str(cm.exception))

    def test_exploratory_run_does_not_touch_the_log(self):
        self.predict(out="scratch")
        self.assertTrue(Path("scratch/gw04_baseline-v1.csv").exists())
        self.assertFalse(ENTRY.exists())


if __name__ == "__main__":
    unittest.main()
