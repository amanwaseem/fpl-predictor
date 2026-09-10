"""Tests for the prediction log contract.

The append-only guarantee is only as good as the write that backs it. These
cover the failure paths — a write that dies partway, a name that is already
taken, a schema that drifted — because those are the ones that would quietly
damage the log rather than the ones that already work.

Every validation test here exists because the check it covers runs *before*
the deadline. After the deadline the entry is immutable under hard rule 1, so
a validator with an untested branch is a hole that can only be discovered once
it is too late to fix.

Run from the repository root: python -m unittest discover tests
"""

import contextlib
import csv
import io
import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fpl.log import (
    FIELDS,
    parse_entry_filename,
    validate_rows,
    write_entry,
)

SPEC = Path(__file__).resolve().parent.parent / "SPEC.md"


def rows(n=3):
    """Model-supplied rows, as predict_baseline builds them: no provenance."""
    return [
        {
            "gameweek": 4, "player_id": i, "web_name": f"P{i}", "team": "ARS",
            "position": "MID", "price": 5.0, "predicted_points": 1.0,
            "expected_minutes": 90.0, "points_per_90": 1.0, "n_fixtures": 1,
            "status": "a",
        }
        for i in range(n)
    ]


def stamped(n=3, **overrides):
    """Complete rows, as validate_rows sees them just before the write."""
    out = []
    for row in rows(n):
        row.update({
            "model_version": "baseline-v1", "snapshot_id": "20260907T141922Z",
            "generated_at_utc": "2026-09-10T09:00:00Z",
            "deadline_utc": "2026-09-12T12:30:00Z",
        })
        row.update(overrides)
        out.append(row)
    return out


class Boom:
    """Raises when the csv writer tries to render it."""

    def __str__(self):
        raise RuntimeError("simulated disk failure mid-write")


class TestWriteEntry(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())

    def write(self, data):
        return write_entry(data, self.out, 4, "baseline-v1", "20260907T141922Z",
                           "2026-09-12T12:30:00Z")

    def test_writes_the_declared_schema(self):
        path = self.write(rows())
        with path.open(newline="") as f:
            reader = csv.DictReader(f)
            self.assertEqual(reader.fieldnames, FIELDS)
            self.assertEqual(len(list(reader)), 3)

    def test_filename_is_zero_padded(self):
        self.assertEqual(self.write(rows()).name, "gw04_baseline-v1.csv")

    def test_stamps_provenance_on_every_row(self):
        path = self.write(rows())
        with path.open(newline="") as f:
            for r in csv.DictReader(f):
                self.assertEqual(r["snapshot_id"], "20260907T141922Z")
                self.assertEqual(r["model_version"], "baseline-v1")
                self.assertTrue(r["generated_at_utc"].endswith("Z"))

    def test_leaves_no_temp_file_behind(self):
        self.write(rows())
        self.assertEqual([p.name for p in self.out.glob("*.tmp")], [])

    def test_refuses_to_overwrite_an_existing_entry(self):
        self.write(rows())
        with self.assertRaises(SystemExit) as cm:
            self.write(rows())
        self.assertIn("append-only", str(cm.exception))

    def test_existing_entry_is_left_untouched(self):
        first = self.write(rows(3)).read_text()
        with self.assertRaises(SystemExit):
            self.write(rows(9))
        self.assertEqual((self.out / "gw04_baseline-v1.csv").read_text(), first)

    def test_refuses_to_write_an_empty_entry(self):
        with self.assertRaises(SystemExit) as cm:
            self.write([])
        self.assertIn("empty", str(cm.exception))
        self.assertEqual(list(self.out.iterdir()), [])

    def test_failure_mid_write_leaves_no_entry(self):
        """The bug this guards: a truncated CSV at the final path would be
        refused by the append-only check on the next run, forcing a manual
        delete inside predictions/."""
        data = rows()
        data[1]["web_name"] = Boom()
        with self.assertRaises(RuntimeError):
            self.write(data)
        self.assertFalse((self.out / "gw04_baseline-v1.csv").exists(),
                         "a partial entry was left at the final path")

    def test_failure_mid_write_leaves_no_temp_file(self):
        data = rows()
        data[1]["web_name"] = Boom()
        with self.assertRaises(RuntimeError):
            self.write(data)
        self.assertEqual([p.name for p in self.out.iterdir()], [])

    def test_can_retry_cleanly_after_a_failed_write(self):
        data = rows()
        data[1]["web_name"] = Boom()
        with self.assertRaises(RuntimeError):
            self.write(data)
        # No manual cleanup needed — the name is still free.
        self.assertTrue(self.write(rows()).exists())


def spec_columns():
    """Column names from the SPEC section 5 table, in document order."""
    section = SPEC.read_text().split("## 5. Prediction log")[1].split("## 6.")[0]
    names = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cell = line.split("|")[1].strip()
        # Skips the header row ("Column") and the "---" separator, both of
        # which are unbackticked.
        if cell.startswith("`") and cell.endswith("`"):
            names.append(cell.strip("`"))
    return names


class TestSchemaMatchesSpec(unittest.TestCase):
    """SPEC section 5 and FIELDS are one contract stated twice.

    Parsed rather than eyeballed because the failure mode is silent drift: a
    column added to the code and not the table, or renamed in the table and
    not the code, is invisible until the scoring harness joins on a column
    that no longer exists — by which time the entries are immutable.
    """

    def test_fields_matches_the_spec_table_exactly(self):
        self.assertEqual(FIELDS, spec_columns())

    def test_the_spec_table_was_actually_found(self):
        # Guards the guard: a SPEC restructure that broke the parser would
        # otherwise make the test above pass vacuously against two empty lists.
        self.assertEqual(len(spec_columns()), 15)


class TestValidateRows(unittest.TestCase):
    """One test per rejection. A validator with an untested branch is a hole."""

    def assertRejects(self, rows_in, because, target_gw=4):
        with self.assertRaises(ValueError) as cm:
            validate_rows(rows_in, target_gw)
        self.assertIn(because, str(cm.exception))

    def test_accepts_a_clean_entry(self):
        # Without this the suite cannot tell a working validator from one that
        # refuses everything.
        self.assertIsNone(validate_rows(stamped(), 4))

    def test_rejects_an_empty_entry(self):
        self.assertRejects([], "at least one row")

    def test_rejects_a_missing_key(self):
        bad = stamped()
        del bad[1]["price"]
        self.assertRejects(bad, "missing ['price']")

    def test_rejects_an_extra_key(self):
        bad = stamped()
        bad[1]["xg"] = 0.4
        self.assertRejects(bad, "unexpected ['xg']")

    def test_rejects_a_non_numeric_prediction(self):
        self.assertRejects(stamped(predicted_points="4.2"), "not a number")

    def test_rejects_a_boolean_prediction(self):
        # bool subclasses int, so this passes a naive isinstance check and is
        # then written to the CSV as the string "True".
        self.assertRejects(stamped(predicted_points=True), "not a number")

    def test_rejects_nan(self):
        self.assertRejects(stamped(predicted_points=float("nan")), "is NaN")

    def test_rejects_inf(self):
        self.assertRejects(stamped(predicted_points=float("inf")), "is inf")

    def test_rejects_a_negative_price(self):
        self.assertRejects(stamped(price=-5.0), "cannot be negative")

    def test_rejects_an_unknown_status(self):
        self.assertRejects(stamped(status="z"), "status is 'z'")

    def test_rejects_a_gameweek_disagreeing_with_the_target(self):
        self.assertRejects(stamped(gameweek=5), "targets GW4")

    def test_allows_negative_predicted_points(self):
        """A red card is -3. A model must be able to predict one."""
        self.assertIsNone(validate_rows(stamped(predicted_points=-1.5), 4))

    def test_reports_every_fault_not_just_the_first(self):
        bad = stamped(3)
        bad[0]["status"] = "z"
        bad[1]["price"] = -1.0
        with self.assertRaises(ValueError) as cm:
            validate_rows(bad, 4)
        message = str(cm.exception)
        self.assertIn("status", message)
        self.assertIn("negative", message)

    def test_caps_how_many_faults_it_prints(self):
        """654 rows with one fault each must not print 654 lines at a deadline."""
        bad = stamped(40, status="z")
        with self.assertRaises(ValueError) as cm:
            validate_rows(bad, 4)
        self.assertIn("and 30 more", str(cm.exception))


class TestSortOrderContract(unittest.TestCase):
    """SPEC section 5: descending predicted_points, ties by ascending player_id."""

    def test_accepts_ties_broken_by_ascending_player_id(self):
        tied = stamped(3)
        for i, row in enumerate(tied):
            row["predicted_points"] = 5.0
            row["player_id"] = i
        self.assertIsNone(validate_rows(tied, 4))

    def test_rejects_ties_in_the_wrong_player_id_order(self):
        tied = stamped(2)
        for row in tied:
            row["predicted_points"] = 5.0
        tied[0]["player_id"], tied[1]["player_id"] = 9, 2
        with self.assertRaises(ValueError) as cm:
            validate_rows(tied, 4)
        self.assertIn("out of the contract sort order", str(cm.exception))

    def test_rejects_ascending_predicted_points(self):
        misordered = stamped(2)
        misordered[0]["predicted_points"] = 1.0
        misordered[1]["predicted_points"] = 9.0
        with self.assertRaises(ValueError) as cm:
            validate_rows(misordered, 4)
        self.assertIn("out of the contract sort order", str(cm.exception))

    def test_names_the_offending_pair(self):
        misordered = stamped(4)
        misordered[2]["predicted_points"] = 9.0
        with self.assertRaises(ValueError) as cm:
            validate_rows(misordered, 4)
        self.assertIn("rows 1 and 2", str(cm.exception))


class TestParseEntryFilename(unittest.TestCase):
    def test_round_trips_a_valid_name(self):
        self.assertEqual(parse_entry_filename("gw04_baseline-v1.csv"),
                         (4, "baseline-v1"))

    def test_round_trips_from_a_full_path(self):
        self.assertEqual(
            parse_entry_filename(Path("predictions/gw38_baseline-v1.csv")),
            (38, "baseline-v1"),
        )

    def test_rejects_an_unpadded_gameweek(self):
        with self.assertRaises(ValueError):
            parse_entry_filename("gw4_baseline-v1.csv")

    def test_rejects_an_underscore_in_the_model_version(self):
        """'gw04_baseline_v1.csv' has two readings, which is the whole reason
        for the constraint."""
        with self.assertRaises(ValueError):
            parse_entry_filename("gw04_baseline_v1.csv")

    def test_rejects_a_non_csv(self):
        with self.assertRaises(ValueError):
            parse_entry_filename("gw04_baseline-v1.json")

    def test_matches_what_write_entry_produces(self):
        out = Path(tempfile.mkdtemp())
        path = write_entry(rows(), out, 4, "baseline-v1", "snap",
                           "2026-09-12T12:30:00Z")
        self.assertEqual(parse_entry_filename(path), (4, "baseline-v1"))


class TestModelVersionAtWriteTime(unittest.TestCase):
    def test_refuses_an_underscore_in_the_model_version(self):
        out = Path(tempfile.mkdtemp())
        with self.assertRaises(SystemExit) as cm:
            write_entry(rows(), out, 4, "baseline_v1", "snap",
                        "2026-09-12T12:30:00Z")
        self.assertIn("two readings", str(cm.exception))
        self.assertEqual(list(out.iterdir()), [])


class TempCwd(unittest.TestCase):
    """Each test gets its own working directory.

    Needed because the post-deadline guard asks whether the target *is*
    predictions/, which is resolved against the working directory.
    """

    def setUp(self):
        self.prev = Path.cwd()
        self.tmp = Path(tempfile.mkdtemp())
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.prev)
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestPostDeadlineGuard(TempCwd):
    """SPEC section 5 rule 5.

    Fatal for predictions/ and advisory elsewhere, because an entry written
    after its deadline cannot be distinguished from one written knowing the
    result — but backfilling a past gameweek into scratch/ is ordinary
    exploratory work and has to keep working.
    """

    PAST = "2020-01-01T00:00:00Z"

    def test_refuses_a_late_write_to_the_prediction_log(self):
        with self.assertRaises(SystemExit) as cm:
            write_entry(rows(), "predictions", 4, "baseline-v1", "snap",
                        self.PAST)
        self.assertIn("after the GW4 deadline", str(cm.exception))
        self.assertFalse((self.tmp / "predictions").exists(),
                         "a late run created predictions/ anyway")

    def test_warns_but_proceeds_for_an_exploratory_run(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            path = write_entry(rows(), "scratch", 4, "baseline-v1", "snap",
                               self.PAST)
        self.assertIn("WARNING", out.getvalue())
        self.assertTrue(path.exists())

    def test_allows_a_write_before_the_deadline(self):
        ahead = datetime.now(timezone.utc) + timedelta(days=1)
        path = write_entry(rows(), "predictions", 4, "baseline-v1", "snap",
                           ahead.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertTrue(path.exists())

    def test_refuses_a_malformed_deadline(self):
        with self.assertRaises(SystemExit) as cm:
            write_entry(rows(), "scratch", 4, "baseline-v1", "snap", "Friday")
        self.assertIn("not a SPEC section 5 timestamp", str(cm.exception))


class TestWriteEntryValidates(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())

    def write(self, data):
        return write_entry(data, self.out, 4, "baseline-v1", "snap",
                           "2026-09-12T12:30:00Z")

    def test_refuses_a_malformed_entry_before_creating_the_file(self):
        bad = rows()
        bad[1]["status"] = "z"
        with self.assertRaises(SystemExit) as cm:
            self.write(bad)
        self.assertIn("Refusing to write a malformed entry", str(cm.exception))
        self.assertEqual(list(self.out.iterdir()), [])

    def test_refuses_rows_out_of_contract_order(self):
        bad = rows(2)
        bad[0]["predicted_points"] = 1.0
        bad[1]["predicted_points"] = 9.0
        with self.assertRaises(SystemExit) as cm:
            self.write(bad)
        self.assertIn("sort order", str(cm.exception))

    def test_refuses_provenance_supplied_by_the_caller(self):
        """A model that stamps its own snapshot_id is forging provenance."""
        bad = rows()
        bad[0]["snapshot_id"] = "not-the-real-snapshot"
        with self.assertRaises(SystemExit) as cm:
            self.write(bad)
        self.assertIn("carrying provenance columns", str(cm.exception))
        self.assertEqual(list(self.out.iterdir()), [])

    def test_leaves_the_callers_rows_untouched(self):
        """The caller's dicts are theirs. Stamping provenance into them means a
        second write of the same rows sees provenance it never set."""
        data = rows()
        before = [dict(r) for r in data]
        self.write(data)
        self.assertEqual(data, before)
