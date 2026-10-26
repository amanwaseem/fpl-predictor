"""Tests for reading the committed log for projection. No database needed."""

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from fpl.db.project import read_log, season_for
from tests.fixtures import TempCwd, read_csv, write_csv
from tests.log_files import write_entry, write_score


class TestSeason(unittest.TestCase):
    def test_august_to_may_is_one_season(self):
        for deadline in (datetime(2026, 8, 14, tzinfo=UTC), datetime(2027, 5, 23, tzinfo=UTC)):
            with self.subTest(deadline=deadline):
                self.assertEqual(season_for(deadline), "2026/27")


class TestReadLog(TempCwd):
    def test_entries_and_scores_become_rows(self):
        write_entry(self.tmp)
        write_score(self.tmp)
        log = read_log()
        self.assertEqual(len(log.entries), 1)
        self.assertEqual(len(log.predictions), 3)
        self.assertEqual(len(log.score_entries), 1)
        self.assertEqual(len(log.player_scores), 3)
        entry = log.entries[0]
        self.assertEqual(
            (entry["season"], entry["gameweek"], entry["model_version"]),
            ("2026/27", 8, "baseline-v1"),
        )
        self.assertEqual(entry["deadline_utc"], datetime(2026, 10, 23, 17, 30, tzinfo=UTC))

    def test_an_unreadable_entry_is_refused(self):
        path = write_entry(self.tmp)
        rows = read_csv(path)
        rows[0]["predicted_points"] = "lots"
        write_csv(path, rows)
        with self.assertRaises(SystemExit) as raised:
            read_log()
        self.assertIn("cannot be projected", str(raised.exception))

    def test_rows_that_disagree_about_provenance_are_refused(self):
        path = write_entry(self.tmp)
        rows = read_csv(path)
        rows[1]["snapshot_id"] = "20261022T080000Z"
        write_csv(path, rows)
        with self.assertRaises(SystemExit):
            read_log()

    def test_a_decimal_keeps_digits_a_float_would_lose(self):
        path = write_entry(self.tmp)
        rows = read_csv(path)
        rows[0]["predicted_points"] = "0.12345678901234567890"
        write_csv(path, rows)
        stored = {p["predicted_points"] for p in read_log().predictions}
        self.assertIn(Decimal("0.12345678901234567890"), stored)

    def test_a_timestamp_without_a_zone_is_refused(self):
        path = write_entry(self.tmp)
        rows = read_csv(path)
        for row in rows:
            row["deadline_utc"] = "2026-10-23T17:30:00"
        write_csv(path, rows)
        with self.assertRaises(SystemExit) as raised:
            read_log()
        self.assertIn("deadline_utc", str(raised.exception))

    def test_a_filename_that_disagrees_with_its_rows_is_refused(self):
        path = write_entry(self.tmp)
        path.rename(path.with_name("gw08_other-v1.csv"))
        with self.assertRaises(SystemExit) as raised:
            read_log()
        self.assertIn("the filename", str(raised.exception))

    def test_a_score_for_an_entry_not_in_the_log_is_refused(self):
        write_entry(self.tmp)
        write_score(self.tmp)
        (self.tmp / "predictions/gw08_baseline-v1.csv").unlink()
        with self.assertRaises(SystemExit) as raised:
            read_log()
        self.assertIn("not in the log", str(raised.exception))

    def test_a_score_whose_filename_disagrees_with_its_contents_is_refused(self):
        write_entry(self.tmp)
        write_entry(self.tmp, gameweek=9)
        stem = write_score(self.tmp)
        for suffix in (".json", ".csv"):
            stem.with_suffix(suffix).rename(stem.with_name("gw09_baseline-v1" + suffix))
        with self.assertRaises(SystemExit) as raised:
            read_log()
        self.assertIn("its filename", str(raised.exception))

    def test_a_score_without_its_rows_is_refused(self):
        write_entry(self.tmp)
        write_score(self.tmp).with_suffix(".csv").unlink()
        with self.assertRaises(SystemExit) as raised:
            read_log()
        self.assertIn("no player rows", str(raised.exception))

    def test_a_score_that_disagrees_with_its_entry_is_refused(self):
        write_entry(self.tmp)
        path = write_score(self.tmp).with_suffix(".csv")
        rows = read_csv(path)
        rows[0]["predicted_points"] = "99.0"
        write_csv(path, rows, fieldnames=list(rows[0]))
        with self.assertRaises(SystemExit) as raised:
            read_log()
        self.assertIn("predicted_points", str(raised.exception))

    def test_cumulative_json_is_not_read_as_a_score(self):
        write_entry(self.tmp)
        write_score(self.tmp)
        (self.tmp / "scores/cumulative.json").write_text("{}")
        self.assertEqual(len(read_log().score_entries), 1)


if __name__ == "__main__":
    unittest.main()
