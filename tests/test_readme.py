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

from fpl.log import roles

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
    """A metric as the README shows it: the four places scores/ stores, rounded
    half up to three, or n/a where scores/ has none."""
    if x is None:
        return "n/a"
    return str(Decimal(str(x)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def points(x):
    return "n/a" if x is None else str(x)


def entries():
    """Every scored entry, from scores/gwNN_<model>.json."""
    return {p.name: json.loads(p.read_text()) for p in sorted(SCORES.glob("gw*_*.json"))}


class TestRecordSoFar(unittest.TestCase):
    def setUp(self):
        self.text = README.read_text()
        self.cumulative = json.loads((SCORES / "cumulative.json").read_text())["models"]
        self.rows = {row[0]: row for row in table_after(self.text, "### The record so far")}

    def test_every_model_has_a_row(self):
        self.assertEqual(set(self.rows), set(self.cumulative) | set(roles.ROLES))

    def test_the_actuals_snapshot_is_the_one_scored_against(self):
        by_snapshot = {}
        for name, e in entries().items():
            by_snapshot.setdefault(e["actuals_snapshot_id"], []).append(name)
        self.assertEqual(len(by_snapshot), 1, f"scores/ mixes actuals snapshots: {by_snapshot}")
        section = self.text[self.text.index("### The record so far") :]
        self.assertIn(f"`{next(iter(by_snapshot))}`", section[: section.index("|")])

    def test_each_row_matches_scores(self):
        for model, row in self.rows.items():
            with self.subTest(model=model):
                _, role, scored, missed, mae, form_mae, xi, form_xi = row
                block = self.cumulative.get(model)
                if block is None:
                    self.assertEqual(role, roles.ROLES.get(model))
                    self.assertEqual([mae, form_mae, xi, form_xi], ["", "", "", ""])
                    continue
                form = block["comparators"].get("form", {})
                self.assertEqual(role, block["role"])
                self.assertEqual(gameweeks(scored), block["gameweeks_scored"])
                self.assertEqual(gameweeks(missed), block["gameweeks_missed"])
                self.assertEqual(mae, three(block["pooled"]["all"]["mae"]))
                self.assertEqual(form_mae, three(form.get("mae")))
                self.assertEqual(xi, points(block["xi"]["captured"]))
                self.assertEqual(form_xi, points(form.get("xi_actual")))


class TestResultsPerGameweek(unittest.TestCase):
    def test_each_row_matches_its_entry(self):
        rows = table_after(README.read_text(), "**Results so far**")
        by_key = {(f"GW{e['gameweek']}", e["model_version"]): e for e in entries().values()}
        self.assertEqual({(r[0], r[1]) for r in rows}, set(by_key))
        for gw, model, mae, rho, form_mae, xi, form_xi in rows:
            with self.subTest(gameweek=gw, model=model):
                e = by_key[(gw, model)]
                form = e["comparators"].get("form", {})
                captured = e["xi"]["captured"]["actual"] if e["xi"]["available"] else None
                self.assertEqual(mae, three(e["metrics"]["all"]["mae"]))
                self.assertEqual(rho, three(e["metrics"]["all"]["spearman"]))
                self.assertEqual(form_mae, three(form.get("mae")))
                self.assertEqual(xi, points(captured))
                self.assertEqual(form_xi, points(form.get("xi_actual")))


if __name__ == "__main__":
    unittest.main()
