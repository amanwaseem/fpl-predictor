"""Tests for reading the committed log for projection. No database needed."""

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from fpl.db.project import as_decimal, read_log, season_for
from tests.fixtures import TempCwd, read_csv, write_csv
from tests.log_files import write_entry, write_score


class TestSeason(unittest.TestCase):

    def test_august_to_may_is_one_season(self):
        for deadline in (datetime(2026, 8, 14, tzinfo=UTC), datetime(2027, 5, 23, tzinfo=UTC)):
            with self.subTest(deadline=deadline):
                self.assertEqual(season_for(deadline), "2026/27")


class TestAsDecimal(unittest.TestCase):

    def test_a_parsed_float_comes_back_as_the_text_it_was(self):
        for text in ("5.12", "0.1", "10.0", "5.125", "-1.5"):
            with self.subTest(text=text):
                self.assertEqual(as_decimal(float(text)), Decimal(text))


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
        self.assertEqual((entry["season"], entry["gameweek"], entry["model_version"]),
                         ("2026/27", 8, "baseline-v1"))
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

    def test_cumulative_json_is_not_read_as_a_score(self):
        write_entry(self.tmp)
        write_score(self.tmp)
        (self.tmp / "scores/cumulative.json").write_text("{}")
        self.assertEqual(len(read_log().score_entries), 1)


if __name__ == "__main__":
    unittest.main()
