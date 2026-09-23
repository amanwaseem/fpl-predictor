"""Tests for fpl/predict_fixture.py, form-fixture-v1.

One class per component the model sums (#32): minutes, attacking returns,
clean sheet, the rest, and the prior they are all shrunk toward. Then a full
run through main() against the verifier, as test_end_to_end does for
baseline-v1, and the import boundary CLAUDE.md sets for new models.

Run from the repository root: python -m unittest discover tests
"""

import ast
import csv
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from fpl import predict_fixture, verify_entry
from fpl.log import FIELDS
from fpl.snapshot import usable_rounds
from tests import fixtures

EVENTS = fixtures.season(4, fixtures.future_deadline())
SNAPSHOT_ID = "20260910T000000Z"
ENTRY = Path("predictions/gw04_form-fixture-v1.csv")


def played(fid, gw, home, away, score_h, score_a):
    return {"id": fid, "event": gw, "team_h": home, "team_a": away,
            "team_h_score": score_h, "team_a_score": score_a, "finished": True,
            "team_h_difficulty": 3, "team_a_difficulty": 3}


def row(fid, gw, home, *, minutes=90, points=2, xg=0.2, goals=0, assists=0, cs=0):
    return {"fixture": fid, "round": gw, "was_home": home, "minutes": minutes,
            "total_points": points, "expected_goals": f"{xg:.2f}",
            "goals_scored": goals, "assists": assists, "clean_sheets": cs}


class League:
    """Clubs 1 and 2 create lots and concede little; 3 and 4 the reverse.

    GW1-3: 1 hosts 3 and 2 hosts 4, every week, 3-0 on big xG. Each club has a
    forward (goals, no clean sheets) and a defender (clean sheets, no goals).
    predict(...) sets GW4 and returns {player id: row}.
    """

    # (player id, club, element_type)
    PLAYERS = [(1, 1, 4), (2, 1, 2), (3, 2, 4), (4, 2, 2),
               (5, 3, 4), (6, 3, 2), (7, 4, 4), (8, 4, 2)]

    def __init__(self):
        self.elements = [fixtures.element(pid, team=club, element_type=pos)
                         for pid, club, pos in self.PLAYERS]
        self.history_fixtures = []
        self.histories = {pid: [] for pid, _, _ in self.PLAYERS}
        self.pasts = {}
        for gw in (1, 2, 3):
            for fid, home, away in ((gw * 10, 1, 3), (gw * 10 + 1, 2, 4)):
                self.history_fixtures.append(played(fid, gw, home, away, 3, 0))
                for pid, club, pos in self.PLAYERS:
                    if club not in (home, away):
                        continue
                    strong = club == home
                    forward = pos == 4
                    self.histories[pid].append(row(
                        fid, gw, strong, points=(8 if forward else 6) if strong else 1,
                        xg=(1.5 if forward else 0.2) if strong else 0.05,
                        goals=int(strong and forward), cs=int(strong and not forward)))

    def bootstrap(self):
        return {"elements": self.elements, "teams": [fixtures.team(t) for t in (1, 2, 3, 4)],
                "element_types": fixtures.ELEMENT_TYPES, "events": EVENTS}

    def predict(self, gw4, **settings):
        fx = self.history_fixtures + [
            {"id": 40 + i, "event": 4, "team_h": h, "team_a": a}
            for i, (h, a) in enumerate(gw4)]
        bootstrap = self.bootstrap()
        usable, _ = usable_rounds(EVENTS, 4)
        return {r["player_id"]: r for r in predict_fixture.predict_rows(
            bootstrap, fx, self.histories, self.pasts, 4, usable, **settings)}


class TestAttack(unittest.TestCase):

    def test_a_weaker_defence_lifts_a_forward(self):
        league = League()
        soft = league.predict([(1, 3), (2, 4)])[1]["predicted_points"]
        hard = league.predict([(1, 2), (3, 4)])[1]["predicted_points"]
        self.assertGreater(soft, hard)

    def test_the_logged_rate_is_before_the_fixture(self):
        """points_per_90 keeps baseline-v1's meaning: the opponent does not move it."""
        league = League()
        self.assertEqual(league.predict([(1, 3), (2, 4)])[1]["points_per_90"],
                         league.predict([(1, 2), (3, 4)])[1]["points_per_90"])


class TestCleanSheet(unittest.TestCase):

    def test_a_weaker_attack_lifts_a_defender(self):
        league = League()
        soft = league.predict([(1, 3), (2, 4)])[2]["predicted_points"]
        hard = league.predict([(1, 2), (3, 4)])[2]["predicted_points"]
        self.assertGreater(soft, hard)

    def test_no_clean_sheet_points_without_sixty_minutes(self):
        """A defender who never lasts 60 minutes cannot earn one.

        Defenders in this league never score, so with no clean sheets either
        he has no part the fixture moves, and the opponent must not matter.
        """
        league = League()
        league.histories[2] = [dict(r, minutes=45, clean_sheets=0)
                               for r in league.histories[2]]
        soft = league.predict([(1, 3), (2, 4)])[2]["predicted_points"]
        hard = league.predict([(1, 2), (3, 4)])[2]["predicted_points"]
        self.assertAlmostEqual(soft, hard)


class TestRest(unittest.TestCase):

    def test_bonus_saves_and_cards_are_not_fixture_scaled(self):
        """With no goals or assists anywhere, a forward has only the rest left.

        His points are then appearances, bonus and the like, and a forward
        earns nothing for a clean sheet, so the opponent must not move his
        prediction. (A defender's would still move: his clean-sheet points
        come from his side's chance of keeping one, not from his history.)
        """
        league = League()
        league.histories = {pid: [dict(r, goals_scored=0, assists=0) for r in rows]
                            for pid, rows in league.histories.items()}
        soft = league.predict([(1, 3), (2, 4)])[1]["predicted_points"]
        hard = league.predict([(1, 2), (3, 4)])[1]["predicted_points"]
        self.assertAlmostEqual(soft, hard)
        self.assertGreater(soft, 0)


class TestMinutes(unittest.TestCase):

    def test_a_blank_predicts_nothing(self):
        rows = League().predict([(2, 4)])
        self.assertEqual(rows[1]["predicted_points"], 0.0)
        self.assertEqual(rows[1]["n_fixtures"], 0)

    def test_a_double_predicts_both_fixtures(self):
        league = League()
        single = league.predict([(1, 3), (2, 4)])[1]["predicted_points"]
        double = league.predict([(1, 3), (2, 4), (3, 1)])[1]["predicted_points"]
        self.assertGreater(double, single * 1.5)

    def test_injured_means_nothing(self):
        league = League()
        league.elements = [dict(e, status="i", chance_of_playing_next_round=0)
                           if e["id"] == 1 else e for e in league.elements]
        self.assertEqual(league.predict([(1, 3), (2, 4)])[1]["predicted_points"], 0.0)


class TestPrior(unittest.TestCase):

    def test_a_strong_last_season_lifts_an_identical_start(self):
        league = League()
        base = league.predict([(1, 3), (2, 4)])[3]["predicted_points"]
        league.pasts[3] = [{"season_name": "2025/26", "minutes": 3000, "total_points": 300,
                            "goals_scored": 25, "assists": 10, "clean_sheets": 0,
                            "expected_goals": "22.0", "expected_assists": "9.0"}]
        self.assertGreater(league.predict([(1, 3), (2, 4)])[3]["predicted_points"], base)

    def test_positional_prior_when_last_season_is_off(self):
        league = League()
        league.pasts[3] = [{"season_name": "2025/26", "minutes": 3000, "total_points": 300}]
        self.assertEqual(league.predict([(1, 3), (2, 4)], last_season=False),
                         League().predict([(1, 3), (2, 4)], last_season=False))


class TestImportBoundary(unittest.TestCase):
    """CLAUDE.md: new models import from snapshot, features and log, not the baseline."""

    def test_imports(self):
        tree = ast.parse(Path(predict_fixture.__file__).read_text())
        imported = {node.module for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module}
        self.assertLessEqual(imported, {"fpl.features", "fpl.log", "fpl.snapshot", "fpl.teams"})


class TestEndToEnd(fixtures.TempCwd):
    """A full run through main(), checked by the verifier, as baseline-v1's is."""

    def setUp(self):
        super().setUp()
        self.deadline = fixtures.future_deadline()
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
                histories[pid] = []
                for f in fx:
                    if f["event"] < 4 and club in (f["team_h"], f["team_a"]):
                        histories[pid].append(row(f["id"], f["event"], f["team_h"] == club,
                                                  minutes=90 - slot * 10, points=2 + slot,
                                                  xg=0.1 * (slot + 1)))
        self.snapshot(SNAPSHOT_ID, elements=elements,
                      teams=[fixtures.team(t) for t in clubs],
                      events=fixtures.season(4, self.deadline), fixtures=fx,
                      histories=histories, pasts={1: [{"season_name": "2025/26",
                                                       "minutes": 3000, "total_points": 150}]},
                      latest=True)

    def predict(self, out="predictions"):
        stream = io.StringIO()
        with redirect_stdout(stream):
            predict_fixture.main(4, out)
        return stream.getvalue()

    def test_run_produces_an_entry_the_verifier_accepts(self):
        output = self.predict()
        self.assertTrue(ENTRY.exists())
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = verify_entry.main(ENTRY)
        self.assertEqual(code, 0, f"verifier refused form-fixture-v1's entry:\n{stream.getvalue()}")
        with ENTRY.open(newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(list(rows[0]), FIELDS)
        self.assertEqual({r["model_version"] for r in rows}, {"form-fixture-v1"})
        self.assertIn("included: 1, 2, 3", output)
        self.assertIn("prior:     1 from 2025/26 / 79 positional", output)

    def test_same_snapshot_same_entry(self):
        """SPEC section 5: byte-identical apart from generated_at_utc."""
        self.predict(out="scratch/a")
        self.predict(out="scratch/b")

        def body(path):
            with open(path, newline="") as f:
                return [{k: v for k, v in r.items() if k != "generated_at_utc"}
                        for r in csv.DictReader(f)]
        self.assertEqual(body("scratch/a/gw04_form-fixture-v1.csv"),
                         body("scratch/b/gw04_form-fixture-v1.csv"))

    def test_exploratory_run_does_not_touch_the_log(self):
        self.predict(out="scratch")
        self.assertTrue(Path("scratch/gw04_form-fixture-v1.csv").exists())
        self.assertFalse(ENTRY.exists())


if __name__ == "__main__":
    unittest.main()
