"""Tests for the human-readable digest.

The digest is derived output and carries no information the entry does not
already have, so the risk it carries is not corruption but misrepresentation:
a table that silently shows the wrong twenty players, or drops a section, is
worse than no digest, because it is the version people will actually read.

Run from the repository root: python -m unittest discover tests
"""

import csv
import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from fpl import summarise
from fpl.log import FIELDS

DEADLINE = "2026-09-12T12:30:00Z"
GENERATED = "2026-09-10T10:52:16Z"


def row(pid, *, predicted, name=None, team="AAA", position="MID", price=5.0,
        minutes=90.0, n_fixtures=1, status="a"):
    return {
        "gameweek": 4, "player_id": pid, "web_name": name or f"p{pid}",
        "team": team, "position": position, "price": price,
        "predicted_points": predicted, "expected_minutes": minutes,
        "points_per_90": predicted, "n_fixtures": n_fixtures,
        "status": status, "model_version": "baseline-v1",
        "snapshot_id": "20260907T141922Z", "generated_at_utc": GENERATED,
        "deadline_utc": DEADLINE,
    }


class DigestCase(unittest.TestCase):
    def setUp(self):
        self.prev = Path.cwd()
        self.tmp = Path(tempfile.mkdtemp())
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.prev)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rows, name="gw04_baseline-v1.csv"):
        path = Path("predictions") / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def digest(self, rows):
        return summarise.summarise(summarise.read_entry(self.write(rows)))


class TestTopN(DigestCase):
    def test_shows_at_most_twenty(self):
        text = self.digest([row(i, predicted=30.0 - i) for i in range(1, 41)])
        self.assertIn("| 20 |", text)
        self.assertNotIn("| 21 |", text)

    def test_shows_the_highest_predictions_first(self):
        text = self.digest([row(1, predicted=1.0, name="LOW"),
                            row(2, predicted=9.0, name="HIGH")])
        self.assertLess(text.index("HIGH"), text.index("LOW"))

    def test_orders_an_unsorted_entry_correctly(self):
        """A scratch entry nothing has verified must still read correctly."""
        text = self.digest([row(1, predicted=1.0, name="LOW"),
                            row(2, predicted=9.0, name="HIGH")][::-1])
        self.assertLess(text.index("HIGH"), text.index("LOW"))

    def test_handles_an_entry_smaller_than_the_table(self):
        text = self.digest([row(1, predicted=5.0)])
        self.assertIn("| 1 |", text)


class TestSections(DigestCase):
    def test_best_in_each_position(self):
        text = self.digest([
            row(1, predicted=9.0, position="MID", name="MIDMAN"),
            row(2, predicted=8.0, position="GKP", name="KEEPER"),
            row(3, predicted=7.0, position="DEF", name="BACK"),
            row(4, predicted=6.0, position="FWD", name="STRIKER"),
        ])
        for name in ("MIDMAN", "KEEPER", "BACK", "STRIKER"):
            self.assertIn(name, text)

    def test_value_ignores_low_predictions(self):
        """A 0.3-point 4.0m defender outranks every real pick on ratio alone."""
        text = self.digest([row(1, predicted=6.0, price=10.0, name="REAL"),
                            row(2, predicted=0.3, price=4.0, name="NOISE")])
        value = text.split("Best value")[1]
        self.assertIn("REAL", value)
        self.assertNotIn("NOISE", value)

    def test_availability_counts_are_spelled_out(self):
        text = self.digest([row(1, predicted=5.0, status="a"),
                            row(2, predicted=0.0, status="i")])
        self.assertIn("available", text)
        self.assertIn("injured", text)

    def test_blanking_players_are_named_by_team(self):
        text = self.digest([row(1, predicted=5.0, team="AAA"),
                            row(2, predicted=0.0, team="BBB", n_fixtures=0)])
        self.assertIn("no GW4 fixture", text)
        self.assertIn("BBB", text)

    def test_no_blank_section_when_everyone_plays(self):
        text = self.digest([row(1, predicted=5.0)])
        self.assertNotIn("no GW4 fixture", text)

    def test_provenance_is_carried_into_the_digest(self):
        text = self.digest([row(1, predicted=5.0)])
        for value in (DEADLINE, GENERATED, "20260907T141922Z", "baseline-v1"):
            self.assertIn(value, text)


class TestFormatting(DigestCase):
    def test_pipe_in_a_name_does_not_break_the_table(self):
        text = self.digest([row(1, predicted=5.0, name="A|B")])
        self.assertIn(r"A\|B", text)

    def test_writes_a_markdown_file_named_after_the_entry(self):
        path = self.write([row(1, predicted=5.0)])
        with redirect_stdout(io.StringIO()):
            summarise.main(str(path), "reports")
        self.assertTrue(Path("reports/gw04_baseline-v1.md").is_file())

    def test_empty_entry_refused(self):
        path = self.write([])
        with self.assertRaises(SystemExit):
            summarise.read_entry(path)

    def test_missing_entry_refused(self):
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
            summarise.main("predictions/nope.csv")


if __name__ == "__main__":
    unittest.main()
