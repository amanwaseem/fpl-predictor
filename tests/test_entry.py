"""Tests for fpl-entry: every logged model, from one snapshot, or nothing.

Against synthetic snapshots with a deadline in the future, so the whole path
runs as it does before a real deadline: write, verify and summarise included.

Run from the repository root: python -m unittest discover tests
"""

import contextlib
import io
import unittest
from pathlib import Path
from unittest import mock

from fpl import entry, predict_baseline
from tests import fixtures
from tests.test_predict_fixture import played, row

SNAPSHOT_ID = "20260910T000000Z"
MODELS = [m for m, _ in entry.LOGGED_MODELS]


def failing(gw, out_dir, snapshot_id):
    raise SystemExit("this model could not run")


def flaky(gw, out_dir, snapshot_id):
    """baseline-v1 under another name, whose real write disagrees with its scratch run."""
    with mock.patch.object(predict_baseline, "MODEL_VERSION", "flaky-v1"):
        predict_baseline.main(gw, out_dir, snapshot_id)
    if Path(out_dir).name == "check":
        return
    path = Path(out_dir) / "gw04_flaky-v1.csv"
    rows = fixtures.read_csv(path)
    rows[0]["points_per_90"] = "99.0"
    fixtures.write_csv(path, rows)


class TestEntry(fixtures.TempCwd):
    def setUp(self):
        super().setUp()
        self.league_with_results(SNAPSHOT_ID)

    def league_with_results(self, name):
        """A league with played fixtures and xG, which form-fixture-v1 needs."""
        clubs = range(1, fixtures.LEAGUE_SIZE + 1)
        fx, histories, elements, pid = [], {}, [], 0
        for gw in (1, 2, 3):
            for i in range(0, len(clubs), 2):
                fx.append(played(gw * 100 + i, gw, clubs[i], clubs[i + 1], (gw + i) % 3, i % 2))
        fx += fixtures.full_round(4, clubs)
        for club in clubs:
            for slot in range(4):
                pid += 1
                elements.append(fixtures.element(pid, team=club, element_type=slot + 1,
                                                 now_cost=45 + slot * 5))
                histories[pid] = [
                    row(f["id"], f["event"], f["team_h"] == club,
                        minutes=90 - slot * 10, points=2 + slot, xg=0.1 * (slot + 1))
                    for f in fx if f["event"] < 4 and club in (f["team_h"], f["team_a"])]
        self.snapshot(name, elements=elements, teams=[fixtures.team(t) for t in clubs],
                      events=fixtures.season(4, fixtures.future_deadline()), fixtures=fx,
                      histories=histories, latest=True)

    def run_entry(self, *args, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = entry.main(*args, **kwargs)
        return code, out.getvalue(), err.getvalue()

    def test_writes_verifies_and_summarises_every_logged_model(self):
        code, out, err = self.run_entry()
        self.assertEqual(code, 0, err)
        for model in MODELS:
            with self.subTest(model=model):
                self.assertTrue(Path(f"predictions/gw04_{model}.csv").exists())
                self.assertTrue(Path(f"reports/gw04_{model}.md").exists())
                self.assertIn(f"### {model}", out)
        self.assertEqual(out.count("OK — entry agrees with snapshot"), 2 * len(MODELS),
                         "each verify prints once live and once in the digest")

    def test_digest_carries_what_the_runbook_says_to_paste(self):
        _, out, _ = self.run_entry()
        text = next(Path("scratch").glob("entry-gw04-*/digest.md")).read_text()
        self.assertIn(text.strip(), out)
        for line in (f"snapshot: `{SNAPSHOT_ID}`", "included: 1, 2, 3", "excluded: none",
                     "prior:", "ratings:", "rows:"):
            self.assertIn(line, text)

    def test_every_model_reads_the_named_snapshot_not_latest(self):
        # A later snapshot becomes LATEST, as a fetch during the run would make it.
        self.league_with_results("20260911T000000Z")
        code, _, err = self.run_entry(SNAPSHOT_ID)
        self.assertEqual(code, 0, err)
        for model in MODELS:
            rows = fixtures.read_csv(f"predictions/gw04_{model}.csv")
            self.assertEqual({r["snapshot_id"] for r in rows}, {SNAPSHOT_ID})

    def test_dry_run_leaves_the_log_and_reports_alone(self):
        code, _, err = self.run_entry(dry_run=True)
        self.assertEqual(code, 0, err)
        self.assertFalse(Path("predictions").exists())
        self.assertFalse(Path("reports").exists())
        run = next(Path("scratch").glob("entry-gw04-*"))
        for model in MODELS:
            self.assertTrue((run / "entry" / f"gw04_{model}.csv").exists())
            self.assertTrue((run / "reports" / f"gw04_{model}.md").exists())

    def test_dry_run_matches_the_real_run_apart_from_generated_at(self):
        self.run_entry(dry_run=True)
        self.run_entry()
        run = next(Path("scratch").glob("entry-gw04-*/entry")).parent
        for model in MODELS:
            dry, real = (fixtures.read_csv(p) for p in
                         (run / "entry" / f"gw04_{model}.csv",
                          f"predictions/gw04_{model}.csv"))
            for r in dry + real:
                del r["generated_at_utc"]
            self.assertEqual(dry, real)

    def test_a_model_failing_its_scratch_run_stops_before_the_log(self):
        models = [*entry.LOGGED_MODELS, ("broken-v1", failing)]
        code, _, err = self.run_entry(models=models)
        self.assertEqual(code, 1)
        self.assertIn("broken-v1: scratch run failed", err)
        self.assertIn("this model could not run", err)
        self.assertFalse(Path("predictions").exists(),
                         "no model may reach the log while another cannot run")

    def test_stops_at_the_first_failed_step_and_names_it(self):
        models = [("flaky-v1", flaky), *entry.LOGGED_MODELS]
        code, _, err = self.run_entry(models=models)
        self.assertEqual(code, 1)
        self.assertIn("flaky-v1: same as the scratch run failed", err)
        self.assertIn("Do not commit a partial set", err)
        self.assertFalse(Path("reports/gw04_flaky-v1.md").exists(), "verify never ran")
        for model in MODELS:
            self.assertFalse(Path(f"predictions/gw04_{model}.csv").exists(),
                             "nothing runs after the first failure")

    def test_refuses_to_run_when_an_entry_is_already_in_the_log(self):
        self.run_entry()
        with self.assertRaises(SystemExit) as raised:
            self.run_entry()
        self.assertIn("Already in the log", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
