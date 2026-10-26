"""Properties of the optimiser over random pools, by Hypothesis (#83).

For any pool in which a legal squad exists:

- every squad the optimiser returns is legal, by fpl.optimise.violations,
  which shares nothing with the model;
- its objective is never below a greedy pick's, on the same pool and budget;
- from any legal squad owned, the plan is legal and never worse than keeping.

The exhaustive check in test_transfers.py proves optimality on small pools;
these look for the rule a hand-built pool would never think to break.
"""

import unittest

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from fpl import optimise

CLUBS = ["ARS", "AVL", "BOU", "BRE", "BHA", "CHE", "CRY", "EVE", "FUL", "LIV", "MCI", "MUN"]
QUOTA = optimise.SQUAD
PROPERTIES = settings(max_examples=40, deadline=None,
                      suppress_health_check=[HealthCheck.too_slow])


@st.composite
def pools(draw):
    players = []
    for position, (low, high) in {"GKP": (2, 4), "DEF": (5, 9), "MID": (5, 9),
                                  "FWD": (3, 6)}.items():
        for _ in range(draw(st.integers(low, high))):
            players.append({
                "player_id": len(players) + 1,
                "web_name": f"p{len(players) + 1}",
                "team": draw(st.sampled_from(CLUBS)),
                "position": position,
                # Mostly cheap, as in FPL, so most pools have a squad within budget.
                "price": draw(st.one_of(st.integers(40, 60), st.integers(40, 60),
                                        st.integers(61, 130))) / 10,
                "predicted_points": draw(st.integers(-100, 1200)) / 100,
                "status": draw(st.sampled_from("aaaad")),
            })
    return players


def objective(squad, bench_weight=optimise.BENCH_WEIGHT):
    """What the optimiser maximises, in its own units: each bench player's points are
    weighted and rounded to the hundredth one by one, as the model counts them."""
    xi = sum(optimise.points(p.predicted_points) for p in squad.xi)
    captain = optimise.points(squad.captain.predicted_points)
    bench = sum(round(optimise.points(p.predicted_points) * bench_weight) for p in squad.bench)
    return xi + captain + bench


def greedy(pool, budget=optimise.BUDGET):
    """Highest points first, wherever it still leaves room to fill the squad. Or None."""
    chosen = []
    for player in sorted(pool, key=lambda p: (-p["predicted_points"], p["player_id"])):
        open_slots = {pos: QUOTA[pos] - sum(c["position"] == pos for c in chosen)
                      for pos in QUOTA}
        if open_slots[player["position"]] == 0:
            continue
        if sum(c["team"] == player["team"] for c in chosen) >= optimise.MAX_PER_CLUB:
            continue
        open_slots[player["position"]] -= 1
        rest = [p for p in pool if p not in chosen and p is not player]
        fill = sum(sum(sorted(p["price"] for p in rest if p["position"] == pos)[:n])
                   for pos, n in open_slots.items())
        if sum(c["price"] for c in chosen) + player["price"] + fill <= budget + 1e-9:
            chosen.append(player)
    return chosen if len(chosen) == 15 else None


def solvable(pool):
    return not optimise.shortfalls(optimise.eligible(pool), optimise.BUDGET)


class TestProperties(unittest.TestCase):

    @PROPERTIES
    @given(pools())
    def test_every_squad_returned_is_legal(self, pool):
        assume(solvable(pool))
        try:
            squad = optimise.best_squad(pool)
        except ValueError:
            # Each rule can be met alone but not together: said, not solved.
            return
        self.assertEqual(optimise.violations(squad), [])

    @PROPERTIES
    @given(pools())
    def test_the_objective_is_never_below_a_greedy_pick(self, pool):
        picked = greedy(optimise.eligible(pool))
        assume(picked is not None)
        best = optimise.best_squad(pool)
        # The greedy squad's own best line-up: the optimiser on just those 15.
        greedy_squad = optimise.best_squad(picked, budget=optimise.BUDGET)
        self.assertGreaterEqual(objective(best), objective(greedy_squad))

    @PROPERTIES
    @given(pools(), st.integers(0, 30).map(lambda tenths: tenths / 10), st.integers(0, 5))
    def test_a_plan_from_any_legal_squad_is_legal_and_no_worse(self, pool, bank, free):
        picked = greedy(optimise.eligible(pool))
        assume(picked is not None)
        current = {p["player_id"]: p["price"] for p in picked}
        plan = optimise.best_transfers(pool, current, bank, free)
        self.assertEqual(optimise.violations(plan.squad), [])
        self.assertGreaterEqual(plan.gain, -1e-9)
        self.assertEqual(plan.hits, max(0, len(plan.transfers) - free))
        self.assertGreaterEqual(plan.bank_after, -1e-9)


if __name__ == "__main__":
    unittest.main()
