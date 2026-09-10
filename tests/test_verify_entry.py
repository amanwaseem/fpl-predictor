"""Tests for the pre-deadline entry verifier.

One test per check, each mutating a clean fixture entry so that exactly the
check under test fails. The clean case is tested too: without it the suite
cannot tell a working verifier from one that refuses everything, and a
verifier that refuses everything fails at the worst possible moment — with an
hour to go and no way to tell a real problem from the tool.

Entries here are built directly rather than by running the baseline, so a test
that fails is telling you about the verifier and not about the model.

Run from the repository root: python -m unittest discover tests
"""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import fixtures
from fpl import verify_entry

SNAPSHOT_ID = "20260910T000000Z"
TARGET_GW = 4
ENTRY_NAME = "gw04_baseline-v1.csv"


class VerifyCase(fixtures.TempCwd):
    """A twenty-team snapshot and an entry that agrees with it on everything."""

    def setUp(self):
        super().setUp()
        self.deadline = fixtures.future_deadline()
        self.dir = self.league(SNAPSHOT_ID, target_gw=TARGET_GW,
                               deadline=self.deadline)
        self.bootstrap = json.loads((self.dir / "bootstrap.json").read_text())
        self.fixture_list = json.loads((self.dir / "fixtures.json").read_text())
        self.rows = fixtures.entry_rows(
            self.bootstrap, self.fixture_list, TARGET_GW, self.deadline,
            snapshot_id=SNAPSHOT_ID,
        )

    def set_bootstrap(self, bootstrap):
        (self.dir / "bootstrap.json").write_text(json.dumps(bootstrap))

    def set_fixtures(self, fixture_list):
        (self.dir / "fixtures.json").write_text(json.dumps(fixture_list))

    def verify(self, rows=None, name=ENTRY_NAME):
        """Run the verifier over an entry, returning (exit code, output)."""
        path = fixtures.write_csv(Path("predictions") / name,
                                  self.rows if rows is None else rows)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = verify_entry.main(path)
        return code, out.getvalue() + err.getvalue()

    def assertRefused(self, rows=None, *, because, name=ENTRY_NAME):
        code, output = self.verify(rows, name=name)
        self.assertEqual(code, 1, f"expected a refusal, but it passed:\n{output}")
        self.assertIn(because, output)
        return output

    def assertAccepted(self, rows=None, name=ENTRY_NAME):
        code, output = self.verify(rows, name=name)
        self.assertEqual(code, 0, f"expected acceptance, but it refused:\n{output}")
        return output

    def rows_for_team(self, short_name):
        return [row for row in self.rows if row["team"] == short_name]


class TestCleanEntry(VerifyCase):
    def test_a_clean_entry_passes(self):
        """The test the rest of the suite is meaningless without."""
        output = self.assertAccepted()
        self.assertIn("OK", output)

    def test_summary_reports_what_was_checked(self):
        """The PR body is the contemporaneous record, and this is its content."""
        output = self.assertAccepted()
        for line in ("snapshot:", "target:", "deadline:", "generated:",
                     "rows:", "blanking:", "doubles:"):
            self.assertIn(line, output)


class TestCoverage(VerifyCase):
    def test_missing_player_rows_refused(self):
        self.assertRefused(self.rows[:-1], because="missing from the entry")

    def test_extra_player_rows_refused(self):
        extra = dict(self.rows[0], player_id=99999)
        self.assertRefused(self.rows + [extra], because="not in the snapshot")

    def test_row_count_reported(self):
        self.assertRefused(self.rows[:-3], because="rows but the snapshot has")


class TestTeams(VerifyCase):
    def test_team_entirely_absent_refused(self):
        without = [row for row in self.rows if row["team"] != "T05"]
        self.assertRefused(without, because="teams are represented")

    def test_absent_team_is_named(self):
        without = [row for row in self.rows if row["team"] != "T05"]
        self.assertRefused(without, because="T05")

    def test_unknown_team_refused(self):
        rows = [dict(row) for row in self.rows]
        for row in rows:
            if row["team"] == "T05":
                row["team"] = "ZZZ"
        self.assertRefused(rows, because="not in the snapshot")


class TestFixtureCounts(VerifyCase):
    """The blank and double check, done by comparison rather than by eye."""

    def test_real_blank_the_entry_missed_refused(self):
        # Teams 1 and 2 are paired in the fixture list; dropping that fixture
        # makes both of them blank, while the entry still claims one fixture.
        self.set_fixtures([f for f in self.fixture_list
                           if 1 not in (f["team_h"], f["team_a"])])
        self.assertRefused(because="(a blank)")

    def test_real_double_the_entry_missed_refused(self):
        self.set_fixtures(self.fixture_list
                          + [fixtures.fixture(TARGET_GW, 1, 3, fid=9001)])
        self.assertRefused(because="(a double)")

    def test_fixtures_in_other_gameweeks_ignored(self):
        """A GW5 fixture must not count toward the GW4 comparison."""
        self.set_fixtures(self.fixture_list
                          + [fixtures.fixture(TARGET_GW + 1, 1, 3, fid=9002)])
        self.assertAccepted()

    def test_blanking_player_with_points_refused(self):
        """The count can be right and the prediction still ignore it."""
        self.set_fixtures([f for f in self.fixture_list
                           if 1 not in (f["team_h"], f["team_a"])])
        rows = [dict(row) for row in self.rows]
        for row in rows:
            if row["team"] in ("T01", "T02"):
                row["n_fixtures"] = 0  # agrees with the fixture list now
        rows.sort(key=lambda r: (-r["predicted_points"], r["player_id"]))
        self.assertRefused(rows, because="carry a non-zero prediction")

    def test_blanking_team_zeroed_passes(self):
        self.set_fixtures([f for f in self.fixture_list
                           if 1 not in (f["team_h"], f["team_a"])])
        rows = fixtures.entry_rows(
            self.bootstrap,
            json.loads((self.dir / "fixtures.json").read_text()),
            TARGET_GW, self.deadline, snapshot_id=SNAPSHOT_ID,
        )
        self.assertAccepted(rows)


class TestAvailability(VerifyCase):
    """A flagged player's prediction has to move, or the flag did nothing."""

    def flag(self, pid, status, chance=None):
        bootstrap = json.loads(json.dumps(self.bootstrap))
        for player in bootstrap["elements"]:
            if player["id"] == pid:
                player["status"] = status
                player["chance_of_playing_next_round"] = chance
        self.set_bootstrap(bootstrap)
        return bootstrap

    def test_flagged_player_with_full_prediction_refused(self):
        self.flag(1, "i")
        self.assertRefused(because="so cannot play")

    def test_suspended_player_with_full_prediction_refused(self):
        self.flag(1, "s")
        self.assertRefused(because="so cannot play")

    def test_flagged_player_zeroed_passes(self):
        bootstrap = self.flag(1, "i")
        rows = fixtures.entry_rows(bootstrap, self.fixture_list, TARGET_GW,
                                   self.deadline, snapshot_id=SNAPSHOT_ID)
        self.assertAccepted(rows)

    def test_partial_chance_not_reduced_refused(self):
        """25% to play cannot mean a full ninety expected minutes."""
        self.flag(1, "d", chance=25)
        self.assertRefused(because="above the")

    def test_partial_chance_reduced_passes(self):
        bootstrap = self.flag(1, "d", chance=25)
        rows = fixtures.entry_rows(bootstrap, self.fixture_list, TARGET_GW,
                                   self.deadline, snapshot_id=SNAPSHOT_ID)
        self.assertAccepted(rows)


class TestProvenance(VerifyCase):
    def test_deadline_not_matching_bootstrap_refused(self):
        rows = [dict(row, deadline_utc="2026-09-19T17:30:00Z")
                for row in self.rows]
        self.assertRefused(rows, because="but the snapshot's GW4 deadline is")

    def test_generated_after_deadline_refused(self):
        rows = [dict(row, generated_at_utc=fixtures.future_deadline(hours=25))
                for row in self.rows]
        self.assertRefused(rows, because="at or after the deadline")

    def test_generated_exactly_at_deadline_refused(self):
        rows = [dict(row, generated_at_utc=self.deadline) for row in self.rows]
        self.assertRefused(rows, because="at or after the deadline")

    def test_model_version_disagreeing_with_filename_refused(self):
        rows = [dict(row, model_version="other-v2") for row in self.rows]
        self.assertRefused(rows, because="in the filename")

    def test_mixed_provenance_refused(self):
        rows = [dict(row) for row in self.rows]
        rows[3]["generated_at_utc"] = fixtures.past_timestamp(hours=9)
        self.assertRefused(rows, because="not the same in every row")

    def test_mixed_snapshot_id_refused(self):
        rows = [dict(row) for row in self.rows]
        rows[3]["snapshot_id"] = "20260101T000000Z"
        self.assertRefused(rows, because="no single snapshot")

    def test_unknown_snapshot_refused(self):
        rows = [dict(row, snapshot_id="20990101T000000Z") for row in self.rows]
        with self.assertRaises(SystemExit) as cm:
            self.verify(rows)
        self.assertIn("No snapshot at", str(cm.exception))


class TestSchema(VerifyCase):
    """Delegated to log.validate_rows, checked here through the CSV."""

    def test_rows_out_of_sort_order_refused(self):
        rows = list(self.rows)
        rows[0], rows[-1] = rows[-1], rows[0]
        self.assertRefused(rows, because="out of the contract sort order")

    def test_wrong_gameweek_refused(self):
        rows = [dict(row, gameweek=5) for row in self.rows]
        self.assertRefused(rows, because="targets GW4")

    def test_bad_status_refused(self):
        rows = [dict(row) for row in self.rows]
        rows[0]["status"] = "z"
        self.assertRefused(rows, because="status is 'z'")

    def test_nan_refused(self):
        rows = [dict(row) for row in self.rows]
        rows[0]["predicted_points"] = float("nan")
        self.assertRefused(rows, because="NaN")

    def test_negative_minutes_refused(self):
        rows = [dict(row) for row in self.rows]
        rows[-1]["expected_minutes"] = -1.0
        self.assertRefused(rows, because="cannot be negative")

    def test_float_in_an_int_column_refused(self):
        """"1.0" in n_fixtures is a fault, not a value to coerce quietly."""
        rows = [dict(row) for row in self.rows]
        rows[0]["n_fixtures"] = "1.0"
        self.assertRefused(rows, because="not an int")

    def test_non_numeric_value_refused(self):
        rows = [dict(row) for row in self.rows]
        rows[0]["predicted_points"] = "n/a"
        self.assertRefused(rows, because="not a number")

    def test_duplicate_player_refused(self):
        rows = list(self.rows) + [dict(self.rows[-1])]
        rows.sort(key=lambda r: (-r["predicted_points"], r["player_id"]))
        self.assertRefused(rows, because="more than once")


class TestFileShape(VerifyCase):
    def test_wrong_column_order_refused(self):
        shuffled = [fixtures.FIELDS[1], fixtures.FIELDS[0], *fixtures.FIELDS[2:]]
        path = fixtures.write_csv(Path("predictions") / ENTRY_NAME, self.rows,
                                  fieldnames=shuffled)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = verify_entry.main(path)
        self.assertEqual(code, 1)
        self.assertIn("column set", out.getvalue() + err.getvalue())

    def test_header_only_entry_refused(self):
        self.assertRefused([], because="no rows")

    def test_unparseable_filename_refused(self):
        with self.assertRaises(SystemExit) as cm:
            self.verify(name="gw4_baseline-v1.csv")
        self.assertIn("zero-padded", str(cm.exception))

    def test_missing_file_refused(self):
        with self.assertRaises(SystemExit) as cm:
            verify_entry.main("predictions/gw09_baseline-v1.csv")
        self.assertIn("No entry at", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
