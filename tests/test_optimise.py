"""Tests for fpl/optimise.py: the best legal squad for one gameweek.

The optimiser is checked three ways: against an exhaustive search on small
pools, against fpl.evaluation.xi's selector on real committed entries, and by its own
rule checker, which shares nothing with the model.
"""

import contextlib
import io
import itertools
import json
import os
import random
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from fpl.evaluation.xi import FORMATIONS, MAX_PER_CLUB, best_xi, formation_of, order_xi
from fpl.log.contract import read_entry
from fpl.optimise import (
    BENCH_WEIGHT,
    SQUAD,
    XI_BOUNDS,
    best_squad,
    cli,
    points,
    tenths,
    violations,
)

ROOT = Path(__file__).resolve().parent.parent
ENTRIES = sorted((ROOT / "predictions").glob("gw*_*.csv"))


def player(pid, position, team, price, predicted, status="a"):
    return {"player_id": pid, "web_name": f"p{pid}", "team": team, "position": position,
            "price": price, "predicted_points": predicted, "status": status}


def random_pool(seed, *, clubs=8):
    """A pool just big enough to choose from: 3 GKP, 6 DEF, 6 MID, 4 FWD."""
    rng = random.Random(seed)
    pool, pid = [], 0
    for position, count in (("GKP", 3), ("DEF", 6), ("MID", 6), ("FWD", 4)):
        for _ in range(count):
            pid += 1
            pool.append(player(pid, position, f"C{rng.randrange(clubs)}",
                               rng.randrange(40, 130) / 10, rng.randrange(0, 900) / 100))
    return pool


def objective(squad, bench_weight):
    """The model's objective for a Squad, in hundredths."""
    starters = sum(points(p.predicted_points) for p in squad.xi)
    bench = sum(round(points(p.predicted_points) * bench_weight) for p in squad.bench)
    return starters + points(squad.captain.predicted_points) + bench


def exhaustive_best(pool, budget, bench_weight):
    """The best objective over every legal squad, found by trying them all.

    Once a squad is fixed, the best XI for a formation is the top players in
    each position, and its best captain is its best player. So each squad
    needs only one pass per formation.
    """
    best = None
    for squad in itertools.combinations(pool, 15):
        if any(sum(p["position"] == pos for p in squad) != n for pos, n in SQUAD.items()):
            continue
        clubs = [p["team"] for p in squad]
        if any(clubs.count(c) > MAX_PER_CLUB for c in set(clubs)):
            continue
        if sum(tenths(p["price"]) for p in squad) > tenths(budget):
            continue
        ranked = {pos: sorted((points(p["predicted_points"]) for p in squad
                               if p["position"] == pos), reverse=True) for pos in SQUAD}
        for d, m, f in FORMATIONS:
            starting = {"GKP": 1, "DEF": d, "MID": m, "FWD": f}
            xi = [v for pos, k in starting.items() for v in ranked[pos][:k]]
            bench = [v for pos, k in starting.items() for v in ranked[pos][k:]]
            value = sum(xi) + max(xi) + sum(round(v * bench_weight) for v in bench)
            best = value if best is None else max(best, value)
    return best


class TestRulesComeFromXi(unittest.TestCase):

    def test_the_xi_bounds_admit_exactly_fpl_xis_formations(self):
        (dl, dh), (ml, mh), (fl, fh) = (XI_BOUNDS[p] for p in ("DEF", "MID", "FWD"))
        admitted = {(d, m, f) for d in range(dl, dh + 1) for m in range(ml, mh + 1)
                    for f in range(fl, fh + 1) if d + m + f == 10}
        self.assertEqual(admitted, set(FORMATIONS))
        self.assertEqual(XI_BOUNDS["GKP"], (1, 1))


class TestAgainstExhaustiveSearch(unittest.TestCase):
    """On pools small enough to try every squad, the solver finds the true optimum."""

    def test_the_optimum_matches_trying_every_squad(self):
        # Eight clubs: every pool feasible, the club cap still in play. Six:
        # mostly infeasible, which the solver has to refuse too.
        pools = [(seed, 8) for seed in range(6)] + [(seed, 6) for seed in range(3)]
        compared = refused = 0
        for seed, clubs in pools:
            pool = random_pool(seed, clubs=clubs)
            # Tight enough that the budget binds, not so tight nothing fits.
            cheapest = sorted(tenths(p["price"]) for p in pool)
            budget = (sum(cheapest[:15]) + 60) / 10
            for bench_weight in (0.0, BENCH_WEIGHT, 1.0):
                with self.subTest(seed=seed, clubs=clubs, bench_weight=bench_weight):
                    expected = exhaustive_best(pool, budget, bench_weight)
                    if expected is None:
                        with self.assertRaises(ValueError):
                            best_squad(pool, budget=budget, bench_weight=bench_weight)
                        refused += 1
                        continue
                    squad = best_squad(pool, budget=budget, bench_weight=bench_weight)
                    self.assertEqual(violations(squad), [])
                    self.assertEqual(objective(squad, bench_weight), expected)
                    compared += 1
        # Guards against pools drifting into all-infeasible, which would turn
        # this into a test of refusals alone.
        self.assertGreaterEqual(compared, 18)
        self.assertGreater(refused, 0)


class TestOnCommittedEntries(unittest.TestCase):

    def test_every_entry_gives_a_legal_squad_at_least_as_good_as_fpl_xi_would_pick(self):
        """fpl.evaluation.xi picks the best XI of the chosen squad; the optimiser's XI and captain
        together can be no worse than that XI with its own best player captained."""
        self.assertTrue(ENTRIES)
        for path in ENTRIES:
            with self.subTest(entry=path.name):
                rows, faults = read_entry(path)
                self.assertEqual(faults, [])
                squad = best_squad(rows, bench_weight=0.0)
                self.assertEqual(violations(squad), [])
                chosen = [vars(p) for p in squad.xi + squad.bench]
                total, xi, _ = best_xi(chosen, lambda p: points(p["predicted_points"]))
                with_captain = total + max(points(p["predicted_points"]) for p in xi)
                self.assertGreaterEqual(points(squad.expected_points), with_captain)

    def test_the_captain_is_the_best_starter_and_the_vice_the_next(self):
        rows, _ = read_entry(ENTRIES[-1])
        squad = best_squad(rows)
        ranked = sorted(squad.xi, key=lambda p: -p.predicted_points)
        self.assertEqual(squad.captain.predicted_points, ranked[0].predicted_points)
        self.assertEqual(squad.vice_captain.predicted_points, ranked[1].predicted_points)


class TestDeterminism(unittest.TestCase):

    def test_ties_go_to_lower_ids_whatever_the_input_order(self):
        """Every player equal: the squad is the lowest ids that make a legal one."""
        pool = [player(pid, pos, f"C{pid % 8}", 5.0, 2.0)
                for pid, pos in enumerate(["GKP"] * 4 + ["DEF"] * 8 + ["MID"] * 8
                                          + ["FWD"] * 6, start=1)]
        first = best_squad(pool)
        shuffled = pool[:]
        random.Random(1).shuffle(shuffled)
        again = best_squad(shuffled)
        ids = sorted(p.player_id for p in first.xi + first.bench)
        self.assertEqual(ids, sorted(p.player_id for p in again.xi + again.bench))
        for position, quota in SQUAD.items():
            lowest = [p["player_id"] for p in pool if p["position"] == position][:quota]
            self.assertTrue(set(lowest) <= set(ids), position)

    def test_a_better_player_is_never_traded_for_a_lower_id(self):
        pool = random_pool(3)
        pool.append(player(999, "MID", "C9", 4.0, 9.99))
        squad = best_squad(pool, budget=200.0)
        self.assertIn(999, [p.player_id for p in squad.xi])


class TestRefusals(unittest.TestCase):
    """No legal squad is refused with its cause, not a solver status or a crash."""

    def assertRefused(self, pool, *words, **kwargs):
        with self.assertRaises(ValueError) as raised:
            best_squad(pool, **kwargs)
        for word in words:
            self.assertIn(word, str(raised.exception))
        return str(raised.exception)

    def test_a_budget_nothing_fits_is_refused_with_the_cheapest_cost(self):
        message = self.assertRefused(random_pool(0), "cheapest 2 GKP, 5 DEF", budget=20.0)
        self.assertNotIn("club", message)

    def test_a_position_with_no_players_is_refused_naming_it(self):
        pool = [p for p in random_pool(0) if p["position"] != "GKP"]
        self.assertRefused(pool, "0 GKP to choose from")

    def test_too_few_clubs_is_refused_naming_the_club_cap(self):
        pool = [p | {"team": f"C{p['player_id'] % 4}"} for p in random_pool(0)]
        self.assertRefused(pool, "4 club(s)", "12 players", budget=500.0)

    def test_a_pool_of_fewer_than_fifteen_names_the_short_positions(self):
        message = self.assertRefused(random_pool(0)[:12], "to choose from")
        self.assertNotIn("fifteen", message)

    def test_an_unknown_position_is_refused(self):
        pool = random_pool(0) + [player(500, "MGR", "C1", 0.5, 0.0)]
        with self.assertRaises(ValueError):
            best_squad(pool)

    def test_the_rule_checker_catches_each_broken_rule(self):
        rows, _ = read_entry(ENTRIES[-1])
        squad = best_squad(rows)
        squad.captain = squad.bench[0]
        squad.vice_captain = squad.bench[1]
        squad.cost = 100.5
        squad.xi = squad.xi[:10]
        found = " ".join(violations(squad))
        for words in ("captain does not start", "vice-captain does not start",
                      "over £100.0m", "10 starters", "distinct"):
            self.assertIn(words, found)


class TestAvailability(unittest.TestCase):
    """The entry's status column decides who may be picked."""

    def pool_with(self, status):
        # The best and cheapest midfielder by far, so he is picked if allowed.
        return random_pool(0) + [player(900, "MID", "C9", 4.0, 9.99, status)]

    def picked(self, pool, **kwargs):
        squad = best_squad(pool, budget=200.0, **kwargs)
        return 900 in [p.player_id for p in squad.xi + squad.bench]

    def test_available_and_doubtful_players_are_picked(self):
        for status in "ad":
            with self.subTest(status=status):
                self.assertTrue(self.picked(self.pool_with(status)))

    def test_injured_suspended_and_not_available_only_when_asked(self):
        for status in "isn":
            with self.subTest(status=status):
                self.assertFalse(self.picked(self.pool_with(status)))
                self.assertTrue(self.picked(self.pool_with(status), include_unavailable=True))

    def test_a_player_gone_from_the_league_never(self):
        self.assertFalse(self.picked(self.pool_with("u"), include_unavailable=True))

    def test_committed_entries_pick_only_available_or_doubtful_players(self):
        for path in ENTRIES:
            with self.subTest(entry=path.name):
                rows, _ = read_entry(path)
                status = {r["player_id"]: r["status"] for r in rows}
                squad = best_squad(rows)
                self.assertTrue(all(status[p.player_id] in "ad"
                                    for p in squad.xi + squad.bench))


class TestTheSolverIsOptional(unittest.TestCase):

    def test_importing_the_module_does_not_import_the_solver(self):
        """So `fpl-optimise --help` works on an install without highspy."""
        code = "import sys, fpl.optimise; print('highspy' in sys.modules)"
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             check=True, cwd=ROOT).stdout.strip()
        self.assertEqual(out, "False")

    def test_without_it_a_solve_says_how_to_install_it(self):
        with mock.patch.dict(sys.modules, {"highspy": None}), \
                self.assertRaises(ImportError) as raised:
            best_squad(random_pool(0), budget=200.0)
        self.assertIn("requirements-optimise.txt", str(raised.exception))


class TestXiHelpersAreShared(unittest.TestCase):

    def test_the_optimisers_xi_is_ordered_and_labelled_as_fpl_xi_does(self):
        rows, _ = read_entry(ENTRIES[-1])
        squad = best_squad(rows)
        xi = [vars(p) for p in squad.xi]
        self.assertEqual(xi, order_xi(xi, lambda p: p["predicted_points"]))
        self.assertEqual(squad.formation, formation_of(xi))


class TestCommand(unittest.TestCase):

    def setUp(self):
        self.previous = Path.cwd()
        os.chdir(ROOT)

    def tearDown(self):
        os.chdir(self.previous)

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli([str(ENTRIES[-1]), *args])
        return code, out.getvalue()

    def test_json_output_is_a_legal_squad(self):
        code, out = self.run_cli("--json")
        self.assertEqual(code, 0)
        squad = json.loads(out)
        self.assertEqual(len(squad["xi"]), 11)
        self.assertEqual(len(squad["bench"]), 4)
        self.assertLessEqual(squad["cost"], 100.0)

    def test_text_output_marks_the_captain_and_the_bench(self):
        _, out = self.run_cli()
        self.assertIn("(C)", out)
        self.assertIn("Bench, in order:", out)

    def test_an_impossible_budget_exits_with_the_reason(self):
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(io.StringIO()):
            self.run_cli("--budget", "10")
        self.assertIn("no legal squad", str(raised.exception))

    def test_include_unavailable_is_accepted(self):
        code, out = self.run_cli("--include-unavailable", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["xi"]), 11)

    def test_a_bench_weight_outside_zero_to_one_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as raised, \
                contextlib.redirect_stderr(io.StringIO()):
            self.run_cli("--bench-weight", "2")
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
