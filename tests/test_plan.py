"""Tests for fpl/plan.py: transfers planned over several gameweeks (#55).

One week is fpl-optimise's problem, so a one-week plan must agree with it.
Over more weeks, synthetic squads whose players change worth from one week to
the next check what one week cannot see: a free transfer rolled to make two
next week, a hit taken only when the weeks after repay it, and the most FPL
banks. Every week's squad is checked by fpl.optimise.violations on the way out.
"""

import contextlib
import io
import json
import unittest
from pathlib import Path

from fpl import plan
from fpl.models import predict_fixture
from fpl.optimise import MAX_FREE_TRANSFERS, best_squad, best_transfers
from fpl.plan import best_plan, horizon_predictions
from tests import fixtures
from tests.test_optimise import random_pool
from tests.test_predict_fixture import rated_league
from tests.test_transfers import even_squad, owned, player


def weeks_of(pool, worth):
    """{gameweek: rows}: `pool` each week, with `worth` {gw: {pid: points}} overriding."""
    return {
        gw: [
            dict(p, predicted_points=worth[gw].get(p["player_id"], p["predicted_points"]))
            for p in pool
        ]
        for gw in sorted(worth)
    }


def tight_budget(pool):
    """As tests/test_optimise: tight enough that the budget binds."""
    cheapest = sorted(round(p["price"] * 10) for p in pool)
    return (sum(cheapest[:15]) + 60) / 10


def ids(squad):
    return sorted(p.player_id for p in squad.xi + squad.bench)


def bought(week):
    return sorted(t.into.player_id for t in week.transfers)


class TestOneWeekIsTheOptimiser(unittest.TestCase):
    def test_from_scratch_it_picks_best_squads_squad(self):
        for seed in range(5):
            with self.subTest(seed=seed):
                pool = random_pool(seed)
                budget = tight_budget(pool)
                one = best_plan({1: pool}, budget=budget)
                self.assertEqual(ids(one.weeks[0].squad), ids(best_squad(pool, budget=budget)))
                self.assertIsNone(one.weeks[0].free_transfers)

    def test_from_a_squad_it_makes_best_transfers_transfers(self):
        squad = even_squad()
        pool = squad + [player(100, "MID", 7.0), player(101, "DEF", 6.0), player(102, "FWD", 4.0)]
        for free in (0, 1, 2):
            with self.subTest(free=free):
                one = best_plan({1: pool}, owned(squad), 10.0, free, bench_weight=0.0)
                single = best_transfers(pool, owned(squad), 10.0, free, bench_weight=0.0)
                self.assertEqual(ids(one.weeks[0].squad), ids(single.squad))
                self.assertEqual(one.weeks[0].hits, single.hits)


class TestAcrossWeeks(unittest.TestCase):
    def setUp(self):
        self.squad = even_squad()
        # Two upgrades worth less than the players they replace this week,
        # and three points more next week.
        self.pool = self.squad + [player(100, "MID", 2.0), player(101, "DEF", 2.0)]

    def plan(self, worth, free=1, **kwargs):
        kwargs.setdefault("discount", 1.0)
        return best_plan(
            weeks_of(self.pool, worth), owned(self.squad), 10.0, free, bench_weight=0.0, **kwargs
        )

    def test_a_free_transfer_is_rolled_to_make_two_next_week(self):
        result = self.plan({1: {}, 2: {100: 6.0, 101: 6.0}})
        first, second = result.weeks
        self.assertEqual(first.transfers, [])
        self.assertEqual(second.free_transfers, 2)
        self.assertEqual(bought(second), [100, 101])
        self.assertEqual(second.hits, 0)

    def test_a_hit_is_taken_only_when_the_weeks_after_repay_it(self):
        # Five more than a starter for two weeks: two transfers now, one a hit,
        # beat one now and one next week (20 - 4 against 15).
        repaid = self.plan({1: {100: 8.0, 101: 8.0}, 2: {100: 8.0, 101: 8.0}})
        self.assertEqual(bought(repaid.weeks[0]), [100, 101])
        self.assertEqual(repaid.weeks[0].hits, 1)
        # Three more for one week only: the hit does not repay itself.
        once = self.plan({1: {100: 6.0, 101: 6.0}, 2: {}})
        self.assertEqual(len(once.weeks[0].transfers), 1)
        self.assertEqual(once.weeks[0].hits, 0)

    def test_the_discount_decides_between_now_and_later(self):
        # A small gain now (102, +1.2 a week) or rolling the transfer for two
        # bigger gains next week (100 and 101, +3 each). Undiscounted, rolling
        # wins (6 against 5.4); with next week at half, the gain now does
        # (3.3 against 3).
        self.pool.append(player(102, "FWD", 4.2))
        worth = {1: {}, 2: {100: 6.0, 101: 6.0}}
        self.assertEqual(self.plan(worth).weeks[0].transfers, [])
        self.assertEqual(bought(self.plan(worth, discount=0.5).weeks[0]), [102])

    def test_free_transfers_bank_up_to_the_most_fpl_allows(self):
        result = self.plan({1: {}, 2: {}, 3: {}}, free=MAX_FREE_TRANSFERS - 1)
        self.assertEqual(
            [w.free_transfers for w in result.weeks],
            [MAX_FREE_TRANSFERS - 1, MAX_FREE_TRANSFERS, MAX_FREE_TRANSFERS],
        )

    def test_from_scratch_the_week_after_has_one_free_transfer(self):
        pool = random_pool(3)
        result = best_plan(weeks_of(pool, {1: {}, 2: {}, 3: {}}), budget=tight_budget(pool))
        self.assertEqual([w.free_transfers for w in result.weeks], [None, 1, 2])

    def test_it_never_does_worse_than_keeping_the_squad_as_it_weighs_them(self):
        for discount, bench_weight in ((1.0, 0.0), (0.9, 0.1), (0.5, 1.0)):
            with self.subTest(discount=discount, bench_weight=bench_weight):
                result = best_plan(
                    weeks_of(self.pool, {1: {100: 6.0}, 2: {101: 9.0}, 3: {}}),
                    owned(self.squad),
                    10.0,
                    1,
                    discount=discount,
                    bench_weight=bench_weight,
                )
                self.assertGreaterEqual(result.value, result.kept_value)

    def test_a_player_sold_and_bought_back_costs_his_price(self):
        # Player 8, a MID, sells for 3.0 but costs 9.0 to buy: worth nothing
        # this week and 10 next. A stand-in (200) is worth 10 this week only.
        # Selling 8 for the stand-in and buying him back next week would be
        # the best plan at his selling price (84 MID points against 77), but
        # buying him back costs 9.0 and the bank has 3.0, so it is not made.
        squad = even_squad()
        squad[7]["price"] = 9.0
        for p in squad[8:12]:
            p["predicted_points"] = 8.0
        current = owned(squad) | {8: 3.0}
        pool = squad + [player(200, "MID", 0.0, price=3.0)]
        result = best_plan(
            weeks_of(pool, {1: {8: 0.0, 200: 10.0}, 2: {8: 10.0, 200: 0.0}}),
            current,
            0.0,
            2,
            discount=1.0,
            bench_weight=0.0,
        )
        self.assertEqual(bought(result.weeks[0]), [200])
        self.assertNotIn(8, bought(result.weeks[1]))
        for week in result.weeks:
            self.assertGreaterEqual(week.bank_after, 0.0)

    def test_the_bank_after_each_week_adds_up(self):
        self.pool[-2]["price"], self.pool[-1]["price"] = 6.0, 7.0
        result = self.plan({1: {100: 6.0}, 2: {101: 6.0}})
        first, second = result.weeks
        # Each sale returns 5.0; 100 costs 6.0 and 101 7.0.
        self.assertEqual((bought(first), first.bank_after), ([100], 9.0))
        self.assertEqual((bought(second), second.bank_after), ([101], 7.0))


class TestRefusals(unittest.TestCase):
    def test_a_discount_below_the_least_is_refused(self):
        squad = even_squad()
        with self.assertRaisesRegex(ValueError, "discount"):
            best_plan({1: squad, 2: squad}, owned(squad), 0.0, 1, discount=0.1)

    def test_a_squad_over_the_club_cap_is_sent_to_the_one_week_optimiser(self):
        squad = even_squad()
        for p in squad[:4]:
            p["team"] = "ARS"
        with self.assertRaisesRegex(ValueError, "fpl-optimise --squad"):
            best_plan({1: squad}, owned(squad), 0.0, 1)

    def test_a_squad_that_cannot_be_planned_from_is_refused(self):
        squad = even_squad()
        with self.assertRaisesRegex(ValueError, "14 players"):
            best_plan({1: squad}, owned(squad[:-1]), 0.0, 1)


class TestHorizonPredictions(fixtures.TempCwd):
    def setUp(self):
        super().setUp()
        self.snap = rated_league(self, fixtures.future_deadline(), later=(5, 6))

    def test_the_weeks_run_from_the_next_and_stop_at_the_season_end(self):
        _, excluded, by_week = horizon_predictions(weeks=5)
        self.assertEqual(sorted(by_week), [4, 5, 6])
        self.assertEqual(excluded, [])
        result = best_plan(by_week, budget=100.0, weeks_requested=5)
        self.assertIn("3 of the 5 gameweeks", plan.describe_plan(result))

    def test_the_first_week_is_form_fixture_v1s_entry(self):
        _, _, by_week = horizon_predictions(weeks=2)
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            predict_fixture.main(4, "scratch")
        entry = sorted(Path("scratch").glob("gw04_*.csv"))[0]
        logged = {r["player_id"]: r for r in fixtures.read_csv(entry)}
        for row in by_week[4]:
            self.assertAlmostEqual(
                float(logged[str(row["player_id"])]["predicted_points"]), row["predicted_points"]
            )

    def test_later_weeks_know_only_what_the_first_deadline_did(self):
        _, _, by_week = horizon_predictions(weeks=3)
        self.assertTrue(all(r["gameweek"] == 5 for r in by_week[5]))
        # A later round settling in the snapshot would not change these: the
        # rounds used are those settled before the first week.
        events = json.loads((self.snap / "bootstrap.json").read_text())["events"]
        self.assertFalse(any(e["data_checked"] for e in events if e["id"] >= 4))


class TestCommand(unittest.TestCase):
    def test_weeks_and_discount_out_of_range_are_usage_errors(self):
        for args in (["--weeks", "0"], ["--weeks", str(plan.MAX_WEEKS + 1)], ["--discount", "0"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    plan.cli(args)
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
