"""Tests for README.md's results: every number in them traces to scores/ (#70).

Rescoring rewrites scores/, so a scoring PR that leaves the README behind
fails here rather than leaving the front page out of date.

Run from the repository root: python -m unittest discover tests
"""

import json
import re
import unittest
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
SCORES = ROOT / "scores"


def table_after(text, heading):
    """The rows of the first Markdown table after `heading`, as lists of cells."""
    rest = text[text.index(heading) :]
    lines = rest.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("|"))
    rows = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip().strip("`") for cell in line.strip("|").split("|")])
    return rows


def gameweeks(cell):
    """'GW4, GW6' -> [4, 6]."""
    return [int(n) for n in re.findall(r"GW(\d+)", cell)]


def three(x):
    """A metric as the README shows it: three places, rounded half up from the
    four that scores/ stores."""
    return str(Decimal(str(x)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def entries():
    """Every scored entry, from scores/gwNN_<model>.json."""
    return [json.loads(p.read_text()) for p in sorted(SCORES.glob("gw*_*.json"))]


class TestRecordSoFar(unittest.TestCase):
    def setUp(self):
        self.text = README.read_text()
        self.cumulative = json.loads((SCORES / "cumulative.json").read_text())["models"]
        self.rows = {row[0]: row for row in table_after(self.text, "### The record so far")}

    def test_every_scored_model_has_a_row(self):
        self.assertLessEqual(set(self.cumulative), set(self.rows))

    def test_the_actuals_snapshot_is_the_one_scored_against(self):
        snapshots = {e["actuals_snapshot_id"] for e in entries()}
        self.assertEqual(len(snapshots), 1)
        section = self.text[self.text.index("### The record so far") :]
        self.assertIn(f"`{snapshots.pop()}`", section[: section.index("|")])

    def test_each_row_matches_scores(self):
        mine = {}
        for e in entries():
            n = e["metrics"]["all"]["n"]
            m = mine.setdefault(e["model_version"], {"n": 0, "form": 0.0, "xi": 0, "form_xi": 0})
            m["n"] += n
            m["form"] += e["comparators"]["form"]["mae"] * n
            m["xi"] += e["xi"]["captured"]["actual"]
            m["form_xi"] += e["comparators"]["form"]["xi_actual"]
        for model, row in self.rows.items():
            with self.subTest(model=model):
                _, role, scored, missed, mae, form_mae, xi, form_xi = row
                block = self.cumulative.get(model)
                if block is None:
                    self.assertEqual([mae, form_mae, xi, form_xi], ["", "", "", ""])
                    continue
                self.assertEqual(role, block["role"])
                self.assertEqual(gameweeks(scored), block["gameweeks_scored"])
                self.assertEqual(gameweeks(missed), block["gameweeks_missed"])
                self.assertEqual(mae, three(block["pooled"]["all"]["mae"]))
                totals = mine[model]
                self.assertEqual(form_mae, three(totals["form"] / totals["n"]))
                self.assertEqual(int(xi), block["xi"]["captured"])
                self.assertEqual(int(form_xi), totals["form_xi"])


class TestResultsPerGameweek(unittest.TestCase):
    def test_each_row_matches_its_entry(self):
        rows = table_after(README.read_text(), "**Results so far**")
        by_key = {(f"GW{e['gameweek']}", e["model_version"]): e for e in entries()}
        self.assertEqual({(r[0], r[1]) for r in rows}, set(by_key))
        for gw, model, mae, rho, form_mae, xi, form_xi in rows:
            with self.subTest(gameweek=gw, model=model):
                e = by_key[(gw, model)]
                self.assertEqual(mae, three(e["metrics"]["all"]["mae"]))
                self.assertEqual(rho, three(e["metrics"]["all"]["spearman"]))
                self.assertEqual(form_mae, three(e["comparators"]["form"]["mae"]))
                self.assertEqual(int(xi), e["xi"]["captured"]["actual"])
                self.assertEqual(int(form_xi), e["comparators"]["form"]["xi_actual"])


if __name__ == "__main__":
    unittest.main()
