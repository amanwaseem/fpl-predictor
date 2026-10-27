"""Tests for the season dimension of the log (#72, ADR 0023).

2026/27 stays in predictions/ and scores/; each later season gets its own
predictions-<season>/ and scores-<season>/. The case the issue names: a
2027/28 GW4 entry written beside 2026/27's GW4, both scored, neither mistaken
for the other, and every 2026/27 file byte-identical before and after.

Run from the repository root: python -m unittest discover tests
"""

import contextlib
import hashlib
import io
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

from fpl.db.project import read_log
from fpl.evaluation import score
from fpl.log.contract import (
    PROVENANCE,
    REPORTS,
    SCORES,
    season_directories,
    season_directory,
    season_of_deadline,
    season_of_directory,
    write_entry,
)
from tests import fixtures
from tests.test_scoring import ACTUALS_AT, ScoringCase

NEXT_PREDICTED_AT = "20270910T000000Z"
NEXT_ACTUALS_AT = "20270920T000000Z"
NEXT_DEADLINE = "2027-09-11T10:30:00Z"


def next_season_events(target_gw):
    """2027/28's events up to `target_gw`, the rounds before it settled."""
    past = [
        fixtures.event(
            gw, deadline=f"2027-08-{13 + gw:02d}T17:30:00Z", data_checked=True, finished=True
        )
        for gw in range(1, target_gw)
    ]
    return past + [fixtures.event(target_gw, deadline="2027-09-25T10:30:00Z", is_next=True)]


def digest(*directories):
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for d in directories
        for p in sorted(Path(d).rglob("*"))
        if p.is_file()
    }


class TestNaming(unittest.TestCase):
    def test_a_season_runs_from_july_to_june(self):
        for deadline, season in (
            ("2026-08-15T17:30:00Z", "2026/27"),
            ("2027-05-23T14:00:00Z", "2026/27"),
            ("2027-06-30T23:59:59Z", "2026/27"),
            ("2027-07-01T00:00:00Z", "2027/28"),
            (datetime(2099, 12, 1, tzinfo=UTC), "2099/00"),
        ):
            with self.subTest(deadline=deadline):
                self.assertEqual(season_of_deadline(deadline), season)

    def test_the_first_season_keeps_the_plain_names(self):
        self.assertEqual(season_directory("2026/27"), Path("predictions"))
        self.assertEqual(season_directory("2026/27", SCORES), Path("scores"))
        self.assertEqual(season_directory("2027/28"), Path("predictions-2027-28"))
        self.assertEqual(season_directory("2027/28", REPORTS), Path("reports-2027-28"))
        self.assertEqual(season_directory("2099/00"), Path("predictions-2099-00"))

    def test_names_read_back_to_their_season(self):
        for season in ("2026/27", "2027/28", "2099/00"):
            for base in ("predictions", SCORES, REPORTS):
                with self.subTest(season=season, base=base):
                    path = Path("/anywhere") / season_directory(season, base)
                    self.assertEqual(season_of_directory(path, base), season)

    def test_other_names_are_not_a_season(self):
        for name in (
            "predictions-old",
            "predictions-2027",
            "predictions-2027-29",
            "predictions-2025-26",
            "predictionsX",
            "scores",
        ):
            with self.subTest(name=name):
                self.assertIsNone(season_of_directory(name))

    def test_seasons_before_the_log_and_malformed_ones_are_refused(self):
        for season in ("2025/26", "2027-28", "2027/29", ""):
            with self.subTest(season=season), self.assertRaises(ValueError):
                season_directory(season)


class TestWriting(fixtures.TempCwd):
    def setUp(self):
        super().setUp()
        snap = self.league(NEXT_PREDICTED_AT, target_gw=4, events=next_season_events(4))
        bootstrap = json.loads((snap / "bootstrap.json").read_text())
        fixture_list = json.loads((snap / "fixtures.json").read_text())
        self.rows = [
            {k: v for k, v in row.items() if k not in PROVENANCE}
            for row in fixtures.entry_rows(bootstrap, fixture_list, 4, NEXT_DEADLINE)
        ]

    def test_the_log_is_the_deadlines_seasons_directory(self):
        path = write_entry(self.rows, None, 4, "baseline-v1", NEXT_PREDICTED_AT, NEXT_DEADLINE)
        self.assertEqual(path, Path("predictions-2027-28/gw04_baseline-v1.csv"))

    def test_another_seasons_directory_is_refused(self):
        with self.assertRaises(SystemExit) as raised:
            write_entry(
                self.rows, "predictions", 4, "baseline-v1", NEXT_PREDICTED_AT, NEXT_DEADLINE
            )
        self.assertIn("predictions-2027-28/", str(raised.exception.code))
        self.assertFalse(Path("predictions").exists())


class TestTwoSeasonsSideBySide(ScoringCase):
    """2026/27's GW4 and 2027/28's GW4, each in its own season's directories."""

    def setUp(self):
        super().setUp()
        self.write_entry()
        self.run_harness()
        self.before = digest("predictions", "scores")

        next_rows = [
            dict(r, deadline_utc=NEXT_DEADLINE, snapshot_id=NEXT_PREDICTED_AT) for r in self.rows
        ]
        fixtures.write_csv(Path("predictions-2027-28/gw04_baseline-v1.csv"), next_rows)
        actual = self.league(
            NEXT_ACTUALS_AT, target_gw=5, latest=True, events=next_season_events(5)
        )
        # A different week: everyone scores one more than predicted.
        fixtures.live(actual, 4, {pid: (pid % 5 + 1, 90) for pid in self.ids})
        with contextlib.redirect_stdout(io.StringIO()):
            score.main(NEXT_ACTUALS_AT)

    def test_each_season_is_scored_into_its_own_directory(self):
        this = json.loads(Path("scores/gw04_baseline-v1.json").read_text())
        nxt = json.loads(Path("scores-2027-28/gw04_baseline-v1.json").read_text())
        self.assertEqual(this["actuals_snapshot_id"], ACTUALS_AT)
        self.assertEqual(nxt["actuals_snapshot_id"], NEXT_ACTUALS_AT)
        self.assertAlmostEqual(this["metrics"]["all"]["mae"], 0.1)
        self.assertAlmostEqual(nxt["metrics"]["all"]["mae"], 1.0)

    def test_the_first_seasons_files_are_untouched(self):
        self.assertEqual(digest("predictions", "scores"), self.before)

    def test_the_projection_holds_both_seasons(self):
        log = read_log()
        self.assertEqual(
            sorted((e["season"], e["gameweek"], e["model_version"]) for e in log.entries),
            [("2026/27", 4, "baseline-v1"), ("2027/28", 4, "baseline-v1")],
        )
        self.assertEqual(sorted(s["season"] for s in log.score_entries), ["2026/27", "2027/28"])
        self.assertEqual(list(season_directories()), ["2026/27", "2027/28"])

    def test_an_entry_in_the_wrong_seasons_directory_is_refused(self):
        # 2026/27's GW4, as another model, among 2027/28's entries.
        Path("predictions-2027-28/gw04_form-fixture-v1.csv").write_text(
            Path("predictions/gw04_baseline-v1.csv")
            .read_text()
            .replace("baseline-v1", "form-fixture-v1")
        )
        for run in (lambda: score.main(NEXT_ACTUALS_AT), read_log):
            with self.subTest(run=run), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    run()
                self.assertIn("2026/27", str(raised.exception.code))


if __name__ == "__main__":
    unittest.main()
