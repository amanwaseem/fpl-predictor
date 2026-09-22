"""Tests for fpl/xi.py, the exact legal-XI selector.

The central claim is exactness, so it is checked the only way that does not
trust the implementation: against brute force over every eleven-player subset
of small random pools, including pools where the club cap binds.

Run from the repository root: python -m unittest discover tests
"""

import random
import unittest
from collections import Counter
from itertools import combinations

from fpl.xi import FORMATIONS, MAX_PER_CLUB, best_xi


def player(pid, position, team, points):
    return {"player_id": pid, "position": position, "team": team, "points": points}


def points(p):
    return p["points"]


def is_legal(xi):
    by_pos = Counter(p["position"] for p in xi)
    by_club = Counter(p["team"] for p in xi)
    shape = (by_pos["DEF"], by_pos["MID"], by_pos["FWD"])
    return (len(xi) == 11 and by_pos["GKP"] == 1 and shape in FORMATIONS
            and max(by_club.values()) <= MAX_PER_CLUB)


def brute_force(pool, value):
    best = None
    for xi in combinations(pool, 11):
        if is_legal(xi):
            total = sum(value(p) for p in xi)
            if best is None or total > best:
                best = total
    return best


def roster(clubs=6, per_club=4, seed=0):
    """A pool with every position represented at every club, random points."""
    rng = random.Random(seed)
    positions = ["GKP", "DEF", "DEF", "MID", "MID", "FWD"]
    pool, pid = [], 0
    for club in range(clubs):
        for slot in range(per_club):
            pid += 1
            pool.append(player(pid, positions[(club + slot) % len(positions)],
                               f"C{club}", rng.randint(-2, 15)))
    return pool


class TestExactness(unittest.TestCase):

    def test_matches_brute_force(self):
        """Random 16-player pools over 4 clubs: the cap binds in most of them."""
        rng = random.Random(42)
        positions = ["GKP", "GKP", "DEF", "DEF", "DEF", "DEF", "DEF", "MID",
                     "MID", "MID", "MID", "MID", "FWD", "FWD", "FWD", "FWD"]
        for trial in range(10):
            pool = [player(i, pos, f"C{rng.randrange(4)}", rng.randint(-1, 12))
                    for i, pos in enumerate(positions)]
            expected = brute_force(pool, points)
            if expected is None:
                with self.assertRaises(ValueError):
                    best_xi(pool, points)
                continue
            total, xi, _ = best_xi(pool, points)
            self.assertEqual(total, expected, f"trial {trial}")
            self.assertTrue(is_legal(xi), f"trial {trial}")

    def test_fractional_values(self):
        """Predictions carry two decimals; hundredths must not be rounded away."""
        # Sixteen players, as above: brute force over 24 is 2.5 million subsets.
        positions = ["GKP", "GKP"] + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 4
        pool = [player(i, pos, f"C{i % 5}", 3 + (i % 7) / 100)
                for i, pos in enumerate(positions)]
        total, xi, _ = best_xi(pool, points)
        self.assertAlmostEqual(total, brute_force(pool, points))


class TestFormations(unittest.TestCase):

    def test_every_legal_shape_is_reachable(self):
        """Eight formations — #3 said seven, but FPL allows 8. Each is reachable."""
        self.assertEqual(sorted(FORMATIONS), [
            (3, 4, 3), (3, 5, 2), (4, 3, 3), (4, 4, 2),
            (4, 5, 1), (5, 2, 3), (5, 3, 2), (5, 4, 1),
        ])
        for defenders, midfielders, forwards in FORMATIONS:
            want = {"DEF": defenders, "MID": midfielders, "FWD": forwards}
            pool, pid = [], 0
            for pos in ("GKP", "DEF", "MID", "FWD"):
                for i in range(5 if pos != "GKP" else 2):
                    pid += 1
                    starter = pos == "GKP" and i == 0 or i < want.get(pos, 0)
                    # Spread over enough clubs that the cap never binds here.
                    pool.append(player(pid, pos, f"C{pid}", 10 if starter else 1))
            with self.subTest(formation=(defenders, midfielders, forwards)):
                _, xi, formation = best_xi(pool, points)
                self.assertEqual(formation, f"{defenders}-{midfielders}-{forwards}")
                self.assertTrue(is_legal(xi))


class TestClubCap(unittest.TestCase):

    def test_cap_binds_and_costs_points(self):
        """Four of the top eleven share a club. The XI must drop one and score less."""
        pool = [player(1, "GKP", "A", 9)]
        pool += [player(2 + i, "DEF", "A", 9) for i in range(4)]  # four from A
        pool += [player(10 + i, "MID", f"M{i}", 8) for i in range(4)]
        pool += [player(20 + i, "FWD", f"F{i}", 8) for i in range(2)]
        pool += [player(30, "DEF", "B", 2), player(31, "MID", "C", 1)]
        unconstrained = sorted((p["points"] for p in pool), reverse=True)[:11]

        total, xi, _ = best_xi(pool, points)
        self.assertTrue(is_legal(xi))
        self.assertLessEqual(Counter(p["team"] for p in xi)["A"], MAX_PER_CLUB)
        self.assertLess(total, sum(unconstrained))


class TestProperties(unittest.TestCase):

    def test_optimum_by_actual_bounds_captured(self):
        """No XI chosen on predictions can beat the XI chosen on actuals."""
        rng = random.Random(7)
        for seed in range(10):
            pool = roster(clubs=8, per_club=5, seed=seed)
            for p in pool:
                p["predicted"] = rng.uniform(0, 8)
            _, by_prediction, _ = best_xi(pool, lambda p: p["predicted"])
            captured = sum(p["points"] for p in by_prediction)
            optimum, _, _ = best_xi(pool, points)
            self.assertGreaterEqual(optimum, captured)

    def test_deterministic_under_ties(self):
        """All-equal values: the same XI every time, regardless of input order."""
        pool = roster(clubs=8, per_club=5)
        for p in pool:
            p["points"] = 3
        _, first, _ = best_xi(pool, points)
        _, second, _ = best_xi(list(reversed(pool)), points)
        self.assertEqual([p["player_id"] for p in first],
                         [p["player_id"] for p in second])



class TestRareCases(unittest.TestCase):

    def test_all_negative_pool(self):
        """A gameweek of red cards: the XI still exists and loses least."""
        positions = ["GKP", "GKP"] + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 4
        pool = [player(i, pos, f"C{i % 6}", -(i % 4) - 1) for i, pos in enumerate(positions)]
        total, xi, _ = best_xi(pool, points)
        self.assertTrue(is_legal(xi))
        self.assertEqual(total, brute_force(pool, points))
        self.assertLess(total, 0)

    def test_exactly_eleven_legal_players(self):
        """The pool is the XI. Nothing to choose, nothing dropped."""
        shape = ["GKP"] + ["DEF"] * 4 + ["MID"] * 4 + ["FWD"] * 2
        pool = [player(i, pos, f"C{i}", 1) for i, pos in enumerate(shape)]
        _, xi, formation = best_xi(pool, points)
        self.assertEqual(sorted(p["player_id"] for p in xi), list(range(11)))
        self.assertEqual(formation, "4-4-2")

    def test_only_one_formation_feasible(self):
        """Two midfielders in the whole pool force 5-2-3, whatever it scores."""
        shape = ["GKP"] + ["DEF"] * 6 + ["MID"] * 2 + ["FWD"] * 4
        pool = [player(i, pos, f"C{i}", 1) for i, pos in enumerate(shape)]
        _, _, formation = best_xi(pool, points)
        self.assertEqual(formation, "5-2-3")

    def test_three_from_one_club_is_allowed(self):
        """The cap is three, not two: the best XI here needs all three from A."""
        pool = [player(1, "GKP", "G", 5)]
        pool += [player(2 + i, "DEF", "A", 10) for i in range(3)]
        pool += [player(10 + i, "DEF", f"D{i}", 1) for i in range(2)]
        pool += [player(20 + i, "MID", f"M{i}", 5) for i in range(5)]
        pool += [player(30 + i, "FWD", f"F{i}", 5) for i in range(3)]
        _, xi, _ = best_xi(pool, points)
        self.assertEqual(Counter(p["team"] for p in xi)["A"], 3)

    def test_input_is_not_reordered_or_mutated(self):
        pool = roster(clubs=8, per_club=5)
        before = [dict(p) for p in pool]
        best_xi(pool, points)
        self.assertEqual(pool, before)


class TestRefusals(unittest.TestCase):

    def test_no_goalkeeper(self):
        pool = [p for p in roster(clubs=8, per_club=5) if p["position"] != "GKP"]
        with self.assertRaises(ValueError):
            best_xi(pool, points)

    def test_too_few_players(self):
        with self.assertRaises(ValueError):
            best_xi(roster(clubs=2, per_club=5), points)

    def test_too_few_clubs(self):
        """Plenty of players, but three clubs can supply at most nine."""
        pool = roster(clubs=3, per_club=12)
        with self.assertRaises(ValueError):
            best_xi(pool, points)

    def test_unknown_position(self):
        pool = roster(clubs=8, per_club=5) + [player(999, "MGR", "C0", 50)]
        with self.assertRaises(ValueError):
            best_xi(pool, points)


if __name__ == "__main__":
    unittest.main()
