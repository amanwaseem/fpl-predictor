"""Tests for the prediction log contract.

The append-only guarantee is only as good as the write that backs it. These
cover the failure paths — a write that dies partway, and a name that is
already taken — because those are the ones that would quietly damage the log
rather than the ones that already work.

Run from the repository root: python -m unittest discover tests
"""

import csv
import tempfile
import unittest
from pathlib import Path

from fpl.log import FIELDS, write_entry


def rows(n=3):
    return [
        {
            "gameweek": 4, "player_id": i, "web_name": f"P{i}", "team": "ARS",
            "position": "MID", "price": 5.0, "predicted_points": 1.0,
            "expected_minutes": 90.0, "points_per_90": 1.0, "n_fixtures": 1,
            "status": "a",
        }
        for i in range(n)
    ]


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
