"""Tests for transfer planning in fpl/optimise.py (#54).

A synthetic squad of fifteen even players with one clear captain, so a
transfer's gain is exactly the points it adds: free transfers are made when
they gain, hits only when the gain clears them, and nothing when it does not.
An exhaustive search over every reachable squad checks the rest.
"""

import contextlib
import io
import itertools
import json
import os
import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import highspy

from fpl import optimise
from fpl.evaluation.xi import FORMATIONS, MAX_PER_CLUB, SCALE
from fpl.log.contract import read_entry
from fpl.optimise import (
    HIT,
    MAX_FREE_TRANSFERS,
    SQUAD,
    best_squad,
    best_transfers,
    check_current,
    cli,
    describe,
    describe_plan,
    points,
    read_squad,
    tenths,
    violations,
)

ROOT = Path(__file__).resolve().parent.parent
ENTRY = sorted((ROOT / "predictions").glob("gw*_*.csv"))[-1]


def player(pid, position, predicted, price=5.0, status="a", team=None):
    return {"player_id": pid, "web_name": f"p{pid}", "team": team or f"C{pid}",
            "position": position, "price": price, "predicted_points": predicted,
            "status": status}


def even_squad():
    """Fifteen players at 3.00, each from their own club, plus a 10.00 captain.

    The captain makes every gain below exact: an upgrade to a 3.00 starter
    adds its difference and nothing to the captaincy.
    """
    positions = ["GKP"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3
    squad = [player(i + 1, pos, 3.0) for i, pos in enumerate(positions)]
    squad[-1]["predicted_points"] = 10.0
    return squad


def owned(squad, selling=None):
    return {p["player_id"]: (selling or {}).get(p["player_id"], p["price"]) for p in squad}


def plan_for(pool, current, bank=0.0, free=1, bench_weight=0.0, **kwargs):
    """With the bench weighted at 0 unless asked, so a gain is a starter's alone."""
    return best_transfers(pool, current, bank, free, bench_weight=bench_weight, **kwargs)


def ins(plan):
    return sorted(t.into.player_id for t in plan.transfers)


class TestTheDoneCriteria(unittest.TestCase):
    """#54's done-when, on a squad where every gain is known exactly."""

    def setUp(self):
        self.squad = even_squad()
        self.current = owned(self.squad)

    def with_upgrade(self, gain, pid=100, **kwargs):
        return self.squad + [player(pid, "MID", 3.0 + gain, **kwargs)]

    def test_the_free_transfer_is_made_when_it_gains(self):
        plan = plan_for(self.with_upgrade(1.0), self.current, free=1)
        self.assertEqual(ins(plan), [100])
        self.assertEqual((plan.hits, plan.hit_points), (0, 0))
        self.assertAlmostEqual(plan.gain, 1.0)

    def test_no_transfer_when_nothing_beats_the_hit(self):
        plan = plan_for(self.with_upgrade(3.0), self.current, free=0)
        self.assertEqual(plan.transfers, [])
        self.assertEqual(plan.gain, 0.0)

    def test_a_hit_is_taken_only_when_the_gain_clears_it(self):
        plan = plan_for(self.with_upgrade(5.0), self.current, free=0)
        self.assertEqual(ins(plan), [100])
        self.assertEqual((plan.hits, plan.hit_points), (1, HIT))
        self.assertAlmostEqual(plan.gain, 5.0 - HIT)

    def test_a_gain_that_only_equals_the_hit_is_not_taken(self):
        plan = plan_for(self.with_upgrade(float(HIT)), self.current, free=0)
        self.assertEqual(plan.transfers, [])

    def test_a_free_transfer_that_gains_nothing_is_rolled(self):
        plan = plan_for(self.with_upgrade(0.0), self.current, free=1)
        self.assertEqual(plan.transfers, [])
        self.assertIn("roll the free transfer", describe_plan(plan))

    def test_a_tie_keeps_the_squad_even_against_a_lower_id(self):
        """Ties go to fewer transfers before lower ids. Here the candidates have
        the lower ids, so only that order keeps the squad."""
        squad = [p | {"player_id": p["player_id"] + 100, "team": f"C{p['player_id'] + 100}"}
                 for p in even_squad()]
        level = squad + [player(1, "MID", 3.0)]
        self.assertEqual(plan_for(level, owned(squad), free=1).transfers, [])
        exactly_the_hit = squad + [player(1, "MID", 3.0 + HIT)]
        self.assertEqual(plan_for(exactly_the_hit, owned(squad), free=0).transfers, [])

    def test_free_transfers_go_to_the_biggest_gains_and_no_further(self):
        pool = self.with_upgrade(2.0, pid=100) + [player(101, "DEF", 4.0)]
        self.assertEqual(ins(plan_for(pool, self.current, free=1)), [100])
        both = plan_for(pool, self.current, free=2)
        self.assertEqual(ins(both), [100, 101])
        self.assertAlmostEqual(both.gain, 3.0)


class TestMoney(unittest.TestCase):

    def setUp(self):
        self.squad = even_squad()
        # Every player bought at 4.5 and now 5.0: each sells for 4.5, so an
        # upgrade at 5.0 needs 0.5 from the bank, whoever is sold. Pricing
        # sales at the current 5.0 would need nothing.
        self.current = owned(self.squad, {p["player_id"]: 4.5 for p in self.squad})
        self.pool = self.squad + [player(100, "DEF", 5.0, price=5.0)]

    def test_the_selling_price_is_what_there_is_to_spend_not_the_current_price(self):
        self.assertEqual(plan_for(self.pool, self.current, bank=0.4).transfers, [])
        plan = plan_for(self.pool, self.current, bank=0.5)
        self.assertEqual(ins(plan), [100])
        self.assertEqual(plan.transfers[0].out.position, "DEF")
        self.assertEqual(plan.transfers[0].selling_price, 4.5)

    def test_the_bank_after_is_the_bank_plus_sales_minus_buys(self):
        plan = plan_for(self.pool, self.current, bank=0.7)
        self.assertEqual(plan.bank_after, round(0.7 + 4.5 - 5.0, 1))


class TestAvailability(unittest.TestCase):

    def test_an_owned_player_of_any_status_may_be_kept(self):
        squad = even_squad()
        squad[5]["status"] = "i"
        plan = plan_for(squad, owned(squad), free=0)
        self.assertEqual(plan.transfers, [])
        self.assertEqual(violations(plan.squad), [])

    def test_a_player_gone_from_the_league_is_sold_with_a_free_transfer(self):
        """He sits on the bench, so selling him gains only bench value: worth a
        free transfer when the bench counts, and nothing when it does not."""
        squad = even_squad()
        squad[8] |= {"status": "u", "predicted_points": 0.0}
        pool = squad + [player(100, "MID", 3.0)]
        plan = plan_for(pool, owned(squad), free=1, bench_weight=0.1)
        self.assertEqual([(t.out.player_id, t.into.player_id) for t in plan.transfers],
                         [(9, 100)])
        self.assertEqual(plan_for(pool, owned(squad), free=1, bench_weight=0.0).transfers, [])

    def test_an_unavailable_player_is_not_bought(self):
        squad = even_squad()
        pool = squad + [player(100, "MID", 9.0, status="i")]
        self.assertEqual(plan_for(pool, owned(squad), free=1).transfers, [])
        self.assertEqual(ins(plan_for(pool, owned(squad), free=1, include_unavailable=True)),
                         [100])


class TestRefusals(unittest.TestCase):

    def assertRefused(self, current, *words, bank=0.0, free=1):
        squad = even_squad()
        with self.assertRaises(ValueError) as raised:
            plan_for(squad + [player(100, "MID", 4.0)], current, bank=bank, free=free)
        for word in words:
            self.assertIn(word, str(raised.exception))

    def test_a_squad_that_cannot_be_planned_from_is_refused_naming_why(self):
        current = owned(even_squad())
        self.assertRefused(dict(list(current.items())[:14]), "14 players")
        self.assertRefused(current | {999: 5.0}, "16 players", "not in the entry")
        swapped = {k: v for k, v in current.items() if k != 1} | {100: 5.0}
        self.assertRefused(swapped, "1 GKP", "6 MID")
        self.assertRefused(current | {1: 0.0}, "above zero")
        self.assertRefused(current, "below zero", bank=-0.1)
        for free in (-1, 6, 1.5):
            with self.subTest(free=free):
                self.assertRefused(current, "free transfers", free=free)


def objective(squad_players, bench_weight):
    """The best line-up of a fixed squad, in fpl.evaluation.xi's units: XI, captain, bench."""
    ranked = {pos: sorted((points(p["predicted_points"]) for p in squad_players
                           if p["position"] == pos), reverse=True) for pos in SQUAD}
    best = None
    for d, m, f in FORMATIONS:
        starting = {"GKP": 1, "DEF": d, "MID": m, "FWD": f}
        xi = [v for pos, k in starting.items() for v in ranked[pos][:k]]
        bench = [v for pos, k in starting.items() for v in ranked[pos][k:]]
        value = sum(xi) + max(xi) + sum(round(v * bench_weight) for v in bench)
        best = value if best is None else max(best, value)
    return best


class TestAgainstExhaustiveSearch(unittest.TestCase):
    """On small pools, the plan's gain equals the best of every reachable squad."""

    def test_the_gain_matches_trying_every_squad(self):
        compared = 0
        seeds = iter(range(100))
        while compared < 24:
            rng = random.Random(next(seeds))
            pool = []
            for pid, pos in enumerate(["GKP"] * 3 + ["DEF"] * 6 + ["MID"] * 6 + ["FWD"] * 4,
                                      start=1):
                pool.append(player(pid, pos, rng.randrange(0, 900) / 100,
                                   price=rng.randrange(40, 100) / 10,
                                   team=f"C{rng.randrange(8)}"))
            legal = [s for s in itertools.combinations(pool, 15)
                     if all(sum(p["position"] == pos for p in s) == n
                            for pos, n in SQUAD.items())
                     and all([p["team"] for p in s].count(c) <= MAX_PER_CLUB
                             for c in {p["team"] for p in s})]
            if not legal:
                continue  # Random clubs left no legal squad; try the next seed.
            start = rng.choice(legal)
            current = {p["player_id"]: max(0.1, p["price"] - rng.choice((0, 0.1, 0.2)))
                       for p in start}
            bank = rng.randrange(0, 20) / 10
            money = tenths(bank) + sum(tenths(v) for v in current.values())
            for free, bench_weight in itertools.product((0, 1, 2), (0.0, 0.1)):
                with self.subTest(compared=compared, free=free, bench_weight=bench_weight):
                    keep = objective(start, bench_weight)
                    best = keep
                    for squad in legal:
                        cost = sum(tenths(current.get(p["player_id"], p["price"]))
                                   for p in squad)
                        if cost > money:
                            continue
                        moved = sum(p["player_id"] not in current for p in squad)
                        value = (objective(squad, bench_weight)
                                 - HIT * SCALE * max(0, moved - free))
                        best = max(best, value)
                    plan = best_transfers(pool, current, bank, free, bench_weight=bench_weight)
                    self.assertEqual(round(plan.gain * SCALE), best - keep)
                    self.assertEqual(plan.hits, max(0, len(plan.transfers) - free))
                    self.assertEqual(violations(plan.squad), [])
                    compared += 1
        self.assertEqual(compared, 24)


class TestOverTheClubCap(unittest.TestCase):
    """A player moving clubs can leave an owned squad over the cap. FPL lets it be
    kept as it is, but no transfer may be made until it is fixed."""

    def setUp(self):
        # Four of the squad at one club; each sells for 5.0.
        self.squad = even_squad()
        for p in self.squad[2:6]:
            p["team"] = "CX"
        self.current = owned(self.squad)

    def test_the_plan_fixes_the_cap_and_says_so(self):
        pool = self.squad + [player(100, "DEF", 3.0)]
        plan = plan_for(pool, self.current, free=1)
        self.assertEqual(plan.over_cap, {"CX": 4})
        self.assertEqual(ins(plan), [100])
        self.assertEqual(plan.transfers[0].out.team, "CX")
        self.assertEqual(violations(plan.squad), [])
        self.assertIn("over the 3-per-club cap", describe_plan(plan))

    def test_fixing_it_is_measured_against_keeping_it_even_at_a_loss(self):
        """The only fix is a worse player, for a hit. The cheapest way is to sell a
        CX defender off the bench, so the XI is unchanged and the loss is the hit."""
        pool = self.squad + [player(100, "DEF", 1.0)]
        plan = plan_for(pool, self.current, free=0)
        self.assertEqual((ins(plan), plan.hits), ([100], 1))
        self.assertNotIn(100, [p.player_id for p in plan.squad.xi])
        self.assertAlmostEqual(plan.xi_gain, -HIT)
        self.assertLess(plan.gain, 0)

    def test_when_nothing_affordable_fixes_it_the_cap_is_named(self):
        pool = self.squad + [player(100, "DEF", 3.0, price=9.0)]
        with self.assertRaises(ValueError) as raised:
            plan_for(pool, self.current, free=1)
        self.assertIn("4 from CX", str(raised.exception))
        self.assertIn("Keep the squad", str(raised.exception))


class TestWhatTheGainCounts(unittest.TestCase):

    def test_a_bench_only_upgrade_gains_nothing_in_the_xi(self):
        """A 0.00 bench player swapped for a 2.00 who still would not start."""
        squad = even_squad()
        squad[8]["predicted_points"] = 0.0
        pool = squad + [player(100, "MID", 2.0)]
        plan = plan_for(pool, owned(squad), free=1, bench_weight=0.1)
        self.assertEqual(ins(plan), [100])
        self.assertEqual(plan.xi_gain, 0.0)
        self.assertAlmostEqual(plan.gain, 0.2)
        text = describe_plan(plan)
        self.assertIn("+0.00 points in the XI", text)
        self.assertIn("+0.20 counting the bench at 0.1", text)

    def test_a_starter_upgrade_counts_in_both(self):
        squad = even_squad()
        plan = plan_for(squad + [player(100, "MID", 4.0)], owned(squad), free=1)
        self.assertAlmostEqual(plan.xi_gain, 1.0)
        self.assertAlmostEqual(plan.gain, 1.0)


class TestRollingFreeTransfers(unittest.TestCase):

    def message(self, free):
        squad = even_squad()
        return describe_plan(plan_for(squad, owned(squad), free=free))

    def test_unused_free_transfers_roll_until_the_most_fpl_banks(self):
        self.assertIn("roll the free transfer.", self.message(1))
        self.assertIn("roll the free transfers.", self.message(2))
        self.assertNotIn("roll", self.message(0))

    def test_at_the_most_fpl_banks_an_unused_one_is_lost(self):
        text = self.message(MAX_FREE_TRANSFERS)
        self.assertNotIn("roll", text)
        self.assertIn("one not used this week is lost", text)


class TestPricesAddUp(unittest.TestCase):

    def test_owned_players_are_shown_at_their_selling_price(self):
        """A player risen from 7.5 to 8.0 sells for 7.7: shown and counted at 7.7."""
        squad = even_squad()
        squad[4]["price"] = 8.0
        current = owned(squad, {squad[4]["player_id"]: 7.7})
        plan = plan_for(squad + [player(100, "MID", 4.0)], current, free=1, bank=1.0)
        everyone = plan.squad.xi + plan.squad.bench
        shown = sum(tenths(p.selling_price if p.selling_price is not None else p.price)
                    for p in everyone)
        self.assertEqual(shown, tenths(plan.squad.cost))
        risen = next(p for p in everyone if p.player_id == squad[4]["player_id"])
        self.assertEqual((risen.price, risen.selling_price), (8.0, 7.7))
        self.assertIn("£  7.7m*", describe(plan.squad))
        bought = next(p for p in everyone if p.player_id == 100)
        self.assertIsNone(bought.selling_price)


class CountingHighs(highspy.Highs):
    """highspy.Highs that counts minimize calls, and can pretend the second solve
    did not finish."""

    minimizes = 0

    def __init__(self, stall=False):
        super().__init__()
        self.stall = stall
        self.stalled = False

    def minimize(self, *args):
        CountingHighs.minimizes += 1
        self.stalled = self.stall
        return super().minimize(*args)

    def getModelStatus(self):
        if self.stalled:
            return highspy.HighsModelStatus.kTimeLimit
        return super().getModelStatus()


def solver_with(**kwargs):
    return SimpleNamespace(Highs=lambda: CountingHighs(**kwargs),
                           HighsModelStatus=highspy.HighsModelStatus)


class TestSolves(unittest.TestCase):

    def test_keeping_the_squad_is_valued_without_a_tie_break_solve(self):
        squad = even_squad()
        CountingHighs.minimizes = 0
        with mock.patch.object(optimise, "_solver", lambda: solver_with()):
            plan_for(squad + [player(100, "MID", 4.0)], owned(squad), free=1)
        self.assertEqual(CountingHighs.minimizes, 1)

    def test_a_tie_break_that_does_not_finish_leaves_the_first_answer(self):
        rows, _ = read_entry(ENTRY)
        expected = best_squad(rows).expected_points
        with mock.patch.object(optimise, "_solver", lambda: solver_with(stall=True)):
            squad = best_squad(rows)
        self.assertEqual(violations(squad), [])
        self.assertEqual(squad.expected_points, expected)


class TestSquadFiles(unittest.TestCase):

    def write(self, data):
        path = Path(tempfile.mkdtemp()) / "squad.json"
        path.write_text(json.dumps(data))
        return path

    def squad_file(self, **changes):
        players = [{"player_id": p["player_id"], "selling_price": p["price"]}
                   for p in even_squad()]
        return {"bank": 0.5, "free_transfers": 1, "players": players} | changes

    def test_a_well_formed_file_reads(self):
        current, bank, free = read_squad(self.write(self.squad_file()))
        self.assertEqual((len(current), bank, free), (15, 0.5, 1))

    def test_a_player_listed_twice_is_named(self):
        data = self.squad_file()
        data["players"][1]["player_id"] = data["players"][0]["player_id"]
        with self.assertRaises(SystemExit) as raised:
            read_squad(self.write(data))
        self.assertIn("more than once: [1]", str(raised.exception))

    def test_free_transfers_must_be_a_whole_number_not_a_bool_or_float(self):
        for value in (True, 2.0, "1"):
            with self.subTest(value=value), self.assertRaises(SystemExit) as raised:
                read_squad(self.write(self.squad_file(free_transfers=value)))
            self.assertIn("not a whole number", str(raised.exception))

    def test_a_bool_is_refused_through_the_api_too(self):
        squad = even_squad()
        self.assertTrue(check_current(squad, owned(squad), 0.0, True))


class TestOnePositionCheck(unittest.TestCase):

    def test_every_selector_refuses_an_unknown_position_alike(self):
        from fpl.evaluation.xi import best_xi
        squad = even_squad()
        pool = squad + [player(100, "MGR", 0.0)]
        calls = {
            "best_xi": lambda: best_xi(pool, lambda p: p["predicted_points"]),
            "best_squad": lambda: best_squad(pool),
            "best_transfers": lambda: plan_for(pool, owned(squad)),
        }
        for name, call in calls.items():
            with self.subTest(selector=name), self.assertRaises(ValueError) as raised:
                call()
            self.assertEqual(str(raised.exception), "unknown position(s) in pool: ['MGR']")


class TestCommand(unittest.TestCase):

    def setUp(self):
        self.previous = Path.cwd()
        os.chdir(ROOT)
        rows, _ = read_entry(ENTRY)
        squad = best_squad(rows)
        self.file = Path(tempfile.mkdtemp()) / "squad.json"
        # What is left of the budget slice 1 spent, so the file describes exactly
        # the squad it chose. A larger bank could afford an upgrade it could not.
        self.file.write_text(json.dumps({
            "bank": round(squad.budget - squad.cost, 1), "free_transfers": 1,
            "players": [{"player_id": p.player_id, "selling_price": p.price}
                        for p in squad.xi + squad.bench]}))

    def tearDown(self):
        os.chdir(self.previous)

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli([str(ENTRY), *args])
        return code, out.getvalue()

    def test_an_optimal_squad_needs_no_transfer(self):
        code, out = self.run_cli("--squad", str(self.file), "--json")
        self.assertEqual(code, 0)
        plan = json.loads(out)
        self.assertEqual(plan["transfers"], [])
        self.assertEqual(plan["gain"], 0.0)

    def test_text_output_explains_the_plan(self):
        _, out = self.run_cli("--squad", str(self.file))
        self.assertIn("keep the squad", out)
        self.assertIn("Bench, in order:", out)

    def test_a_malformed_squad_file_says_what_it_expected(self):
        self.file.write_text('{"bank": 0.5}')
        with self.assertRaises(SystemExit) as raised:
            self.run_cli("--squad", str(self.file))
        self.assertIn("free_transfers", str(raised.exception))

    def test_budget_and_squad_together_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_cli("--squad", str(self.file), "--budget", "90")
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
