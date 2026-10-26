"""The best legal squad for one gameweek, by integer programming (#53, ADR 0010).

    fpl-optimise predictions/gw06_form-fixture-v1.csv
    fpl-optimise scratch/gw08_baseline-v1.csv --budget 98.5 --json
    fpl-optimise predictions/gw06_form-fixture-v1.csv --squad my-squad.json

Given one entry's predictions, picks the fifteen players, the starting eleven
and the captain that maximise expected points:

- £100.0m budget, on the entry's `price` column;
- 2 GKP, 5 DEF, 5 MID, 3 FWD, and at most three players from any club;
- a legal XI, with the formations and the club cap taken from fpl.evaluation.xi, so the
  optimiser and the scoring harness cannot disagree about what is legal;
- the captain scores double;
- each bench player counts at BENCH_WEIGHT of their points;
- only players available or doubtful in the entry's status column, unless
  --include-unavailable admits the injured and suspended (never those gone
  from the league).

Squad, XI and captain are chosen together in one model rather than in turn:
the best squad depends on who would start, and greedy picking is wrong once
the budget and the club cap interact (ADR 0010).

## Transfers

With --squad, it plans transfers from a squad already owned instead (#54). The
file gives the fifteen players' ids and selling prices, the bank, and the free
transfers:

    {"bank": 0.5, "free_transfers": 1,
     "players": [{"player_id": 16, "selling_price": 10.1}, ...]}

It is the same model with more in it, not a second one:

- a player kept costs his selling price, and the money to spend is the bank
  plus what the squad sells for;
- each transfer beyond the free ones costs HIT points;
- ties go to fewer transfers, so a transfer that gains nothing is not made.

The gain is measured against the best line-up of the squad as it stands, net
of hits, so a transfer is suggested only when it pays for itself. It is given
twice: in the XI's expected points, and as the optimiser counts it, with the
bench at BENCH_WEIGHT. A squad pushed over the club cap by a player moving
clubs may be kept, as FPL allows, but any transfer must fix it, so the plan
does, and says so, even when that scores less than keeping it.

## Exact, and settled toward lower ids

Points are counted in fpl.evaluation.xi's hundredths and prices in tenths, so every
coefficient is an integer and the optimum is exact to the last hundredth: the
solver's gap is set to zero, not left at its default of 0.01%.

Ties are common among cheap players predicted 0.00. A second solve holds the
points at the optimum and prefers lower player ids, so a tie-break can never
cost a hundredth of a point (best_squad says how). A pool in which no legal
squad exists is refused with the reason, before the solver is asked.

Every solution is checked against the rules, by code that shares nothing with
the model, before it is returned. Writes nothing; reads the entry it is given.
Needs the HiGHS solver: uv sync --group optimise.
"""

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from fpl.evaluation.xi import (
    FORMATIONS,
    MAX_PER_CLUB,
    POSITIONS,
    SCALE,
    XI_SIZE,
    check_positions,
    formation_of,
    order_xi,
)
from fpl.log.contract import read_entry
from fpl.paths import require_project_root

BUDGET = 100.0
SQUAD = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
# A bench player scores only when a starter does not play. Weighting the bench
# at a tenth makes the optimiser prefer a bench that might score over one that
# will not, at the same price, without letting bench points buy a worse XI.
BENCH_WEIGHT = 0.1
# Points each transfer beyond the free ones costs, and the most free
# transfers FPL lets a manager bank.
HIT = 4
MAX_FREE_TRANSFERS = 5

# Who may be picked, by the entry's status column. Available and doubtful
# players always; injured, suspended and not-available ones only when asked,
# since FPL lets a manager own them but an optimiser should not pick them as
# cheap filler. Never "u": gone from the league, and unable to score at all.
AVAILABLE = frozenset("ad")
UNAVAILABLE = frozenset("isn")

# Starters per position, from fpl.evaluation.xi's formations rather than a second copy:
# 1 GKP, and each outfield position between its fewest and most in any
# formation. With eleven starters in all, these bounds admit exactly the
# formations fpl.evaluation.xi lists; tests/test_optimise.py checks that.
XI_BOUNDS = {"GKP": (1, 1)} | {
    position: (min(f[i] for f in FORMATIONS), max(f[i] for f in FORMATIONS))
    for i, position in enumerate(("DEF", "MID", "FWD"))
}


@dataclass
class Pick:
    player_id: int
    web_name: str
    team: str
    position: str
    price: float
    predicted_points: float
    # Set only for a player already owned: what he sells for, which is what he
    # costs this squad. The Squad's cost counts this, not `price`.
    selling_price: float | None = None


@dataclass
class Squad:
    xi: list[Pick]
    bench: list[Pick]
    captain: Pick
    vice_captain: Pick
    formation: str
    cost: float
    budget: float
    # The XI's predicted points with the captain's counted twice.
    expected_points: float
    bench_points: float


class SolverTimeout(Exception):
    """The solver ran out of time before it proved an optimum."""


class SolverFailure(Exception):
    """The solver stopped for a reason that is not the request's: not infeasible, not time."""


def _deadline(time_limit):
    """A monotonic deadline `time_limit` seconds away, or None for no limit."""
    return None if time_limit is None else time.monotonic() + time_limit


def tenths(value):
    """A price in tenths of a million, as the API counts it: 10.0 is 100."""
    return round(value * 10)


def points(value):
    """Predicted points in fpl.evaluation.xi's units, so the two count points alike."""
    return round(value * SCALE)


def _solver():
    """highspy, imported only when a squad is solved, so --help works without it."""
    try:
        import highspy
    except ImportError:
        raise ImportError(
            "fpl-optimise needs the HiGHS solver, which is not installed. "
            "Install it with: uv sync --group optimise"
        ) from None
    return highspy


def eligible(players, include_unavailable=False):
    """The players the optimiser may pick, by status (see AVAILABLE)."""
    allowed = AVAILABLE | (UNAVAILABLE if include_unavailable else frozenset())
    return [p for p in players if p.get("status", "a") in allowed]


def shortfalls(pool, budget):
    """Why no legal squad can exist, before asking the solver: readable lines.

    Each check is necessary rather than sufficient, so an empty list does not
    promise a squad, but a non-empty one names the cause instead of a solver
    status.
    """
    found = []
    for position, quota in SQUAD.items():
        have = sum(p["position"] == position for p in pool)
        if have < quota:
            found.append(f"{have} {position} to choose from, and a squad needs {quota}")
    clubs = {p["team"] for p in pool}
    room = sum(min(MAX_PER_CLUB, sum(p["team"] == club for p in pool)) for club in clubs)
    if room < sum(SQUAD.values()):
        found.append(
            f"{len(clubs)} club(s) at most {MAX_PER_CLUB} each allow only {room} "
            f"players, and a squad needs {sum(SQUAD.values())}"
        )
    if not found:
        cheapest = sum(
            sum(sorted(tenths(p["price"]) for p in pool if p["position"] == position)[:quota])
            for position, quota in SQUAD.items()
        )
        if cheapest > tenths(budget):
            found.append(
                f"the cheapest 2 GKP, 5 DEF, 5 MID and 3 FWD cost "
                f"£{cheapest / 10:.1f}m, over £{budget:.1f}m"
            )
    return found


def best_squad(
    players, budget=BUDGET, bench_weight=BENCH_WEIGHT, include_unavailable=False, time_limit=None
):
    """The squad, XI and captain maximising expected points, as a Squad.

    `players` are dicts with player_id, web_name, team, position, price and
    predicted_points, and optionally status, as fpl.log.contract.read_entry returns
    them. Raises ValueError, naming the cause, if no legal squad fits, and
    SolverTimeout if no optimum is proved within `time_limit` seconds.
    """
    check_positions(players)
    pool = sorted(eligible(players, include_unavailable), key=lambda p: p["player_id"])
    reasons = shortfalls(pool, budget)
    if reasons:
        raise ValueError("no legal squad: " + "; ".join(reasons))
    cost = {p["player_id"]: tenths(p["price"]) for p in pool}
    squad, starters, captain, _ = _solve(
        pool, cost, tenths(budget), bench_weight, deadline=_deadline(time_limit)
    )
    return _squad(squad, starters, captain, cost, budget)


def _solve(
    pool, cost, budget, bench_weight, owned=None, free_transfers=0, keep=False, deadline=None
):
    """Build the model and solve it: (squad, starters, captain, value).

    `cost` maps player id to tenths of a million and `budget` is in tenths.
    With `owned`, a set of player ids, buying anyone else is a transfer and
    each beyond `free_transfers` costs HIT points. `keep` forbids transfers and
    drops the club cap, which FPL enforces only when a transfer is made, so a
    squad pushed over it by a player moving clubs may still be kept as it is.
    `value` is the optimum in fpl.evaluation.xi's units, net of hits.

    The first solve maximises expected points alone, with small integer
    coefficients and a zero gap, so its optimum is exact. The second holds the
    points at that optimum and settles ties: toward fewer transfers, so a
    transfer that gains nothing is not suggested, then toward lower player ids
    by the sum of the chosen players' positions in id order. Kept apart, a
    tie-break can never be weighed against points, and no coefficient is large
    enough for the solver's tolerances to matter. Ties that survive an equal
    sum (rare) are the solver's to break, consistently for the pinned HiGHS.

    With `keep` the squad is fixed and only its value is wanted, so the second
    solve is skipped. If the second solve does not finish optimal, the first
    solve's answer stands: just as good in points, only not tie-broken.

    `deadline` is a time.monotonic() value both solves must finish by. A first
    solve that reaches it raises SolverTimeout: an answer not proved optimal is
    not given.
    """
    highspy = _solver()
    n = len(pool)
    model = highspy.Highs()
    model.silent()
    # The objectives are integral, so a gap below one unit is optimality.
    model.setOptionValue("mip_rel_gap", 0.0)
    model.setOptionValue("mip_abs_gap", 0.5)

    def limit_time():
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SolverTimeout("the solver's time limit was reached")
            model.setOptionValue("time_limit", remaining)

    squad = model.addBinaries(n)
    start = model.addBinaries(n)
    captain = model.addBinaries(n)

    for i in range(n):
        model.addConstr(start[i] <= squad[i])
        model.addConstr(captain[i] <= start[i])
    model.addConstr(model.qsum(captain[i] for i in range(n)) == 1)
    model.addConstr(model.qsum(start[i] for i in range(n)) == XI_SIZE)
    for position in POSITIONS:
        members = [i for i in range(n) if pool[i]["position"] == position]
        model.addConstr(model.qsum(squad[i] for i in members) == SQUAD[position])
        low, high = XI_BOUNDS[position]
        model.addConstr(model.qsum(start[i] for i in members) >= low)
        model.addConstr(model.qsum(start[i] for i in members) <= high)
    for club in sorted({p["team"] for p in pool}) if not keep else ():
        model.addConstr(
            model.qsum(squad[i] for i in range(n) if pool[i]["team"] == club) <= MAX_PER_CLUB
        )
    model.addConstr(model.qsum(cost[pool[i]["player_id"]] * squad[i] for i in range(n)) <= budget)

    # Expected points in fpl.evaluation.xi's units: a starter's, again for the captain,
    # and bench_weight of a bench player's.
    worth = [points(p["predicted_points"]) for p in pool]
    bench = [round(worth[i] * bench_weight) for i in range(n)]
    value = model.qsum(
        worth[i] * start[i] + worth[i] * captain[i] + bench[i] * (squad[i] - start[i])
        for i in range(n)
    )

    transfers = None
    if owned is not None:
        bought = [i for i in range(n) if pool[i]["player_id"] not in owned]
        transfers = model.qsum(squad[i] for i in bought)
        if keep:
            model.addConstr(transfers == 0)
        # The hits beyond the free transfers: maximising makes this exactly
        # max(0, transfers - free_transfers).
        hits = model.addIntegral(lb=0, ub=sum(SQUAD.values()))
        model.addConstr(hits >= transfers - free_transfers)
        value = value - HIT * SCALE * hits

    limit_time()
    model.maximize(value)
    status = model.getModelStatus()
    if status == highspy.HighsModelStatus.kTimeLimit:
        raise SolverTimeout("the solver's time limit was reached before it proved an optimum")
    # Every variable is bounded, so "unbounded or infeasible" can only be infeasible.
    if status not in (
        highspy.HighsModelStatus.kOptimal,
        highspy.HighsModelStatus.kInfeasible,
        highspy.HighsModelStatus.kUnboundedOrInfeasible,
    ):
        raise SolverFailure(
            f"the solver stopped without an answer: {model.modelStatusToString(status)}"
        )
    if status != highspy.HighsModelStatus.kOptimal:
        raise ValueError(
            f"no legal squad within £{budget / 10:.1f}m: each rule can be met alone, but not "
            "together; the budget and the three-per-club cap leave no squad (solver: "
            f"{model.modelStatusToString(model.getModelStatus())})."
        )
    best = round(model.getObjectiveValue())
    solution = [model.vals(variables) for variables in (squad, start, captain)]

    if not keep:
        # Half a unit below the optimum, so the solver's tolerances cannot
        # exclude the optimum itself, and still above every worse squad.
        model.addConstr(value >= best - 0.5)
        rank = model.qsum(i * (squad[i] + start[i] + captain[i]) for i in range(n))
        # Fewer transfers first: one transfer outweighs any difference in
        # rank, which is at most 27 chosen variables each ranked below n.
        try:
            limit_time()
            model.minimize(rank if transfers is None else (27 * n + 1) * transfers + rank)
            tie_broken = model.getModelStatus() == highspy.HighsModelStatus.kOptimal
        except SolverTimeout:
            tie_broken = False
        if tie_broken:
            solution = [model.vals(variables) for variables in (squad, start, captain)]

    chosen_squad, starters, captains = (
        [pool[i] for i, v in enumerate(values) if v > 0.5] for values in solution
    )
    return chosen_squad, starters, captains[0], best


def _pick(player, selling=None):
    return Pick(
        player_id=player["player_id"],
        web_name=player["web_name"],
        team=player["team"],
        position=player["position"],
        price=player["price"],
        predicted_points=player["predicted_points"],
        selling_price=(selling or {}).get(player["player_id"]),
    )


def _squad(squad, starters, captain, cost, budget, selling=None, verify=True):
    """A Squad from the chosen players, checked against the rules unless `verify`
    is off (for a squad kept as it is, which may be over the club cap)."""
    xi = order_xi(starters, lambda p: p["predicted_points"])
    starting = {p["player_id"] for p in xi}
    # FPL's bench order: the reserve goalkeeper first, then outfield players
    # in the order they would come on, best first.
    bench = sorted(
        (p for p in squad if p["player_id"] not in starting),
        key=lambda p: (p["position"] != "GKP", -p["predicted_points"], p["player_id"]),
    )
    vice = max(
        (p for p in xi if p["player_id"] != captain["player_id"]),
        key=lambda p: (p["predicted_points"], -p["player_id"]),
    )
    result = Squad(
        xi=[_pick(p, selling) for p in xi],
        bench=[_pick(p, selling) for p in bench],
        captain=_pick(captain, selling),
        vice_captain=_pick(vice, selling),
        formation=formation_of(xi),
        cost=sum(cost[p["player_id"]] for p in squad) / 10,
        budget=budget,
        expected_points=(
            sum(points(p["predicted_points"]) for p in xi) + points(captain["predicted_points"])
        )
        / SCALE,
        bench_points=sum(points(p["predicted_points"]) for p in bench) / SCALE,
    )
    broken = violations(result) if verify else []
    if broken:
        raise AssertionError("the solver returned an illegal squad: " + "; ".join(broken))
    return result


# --- Transfers (#54) ---------------------------------------------------------


@dataclass
class Transfer:
    out: Pick
    # What FPL pays for him: his purchase price plus half of any rise, rounded
    # down. Given, not guessed: reading it needs the manager's login.
    selling_price: float
    into: Pick


@dataclass
class Plan:
    transfers: list[Transfer]
    free_transfers: int
    # Transfers beyond the free ones, and the points they cost.
    hits: int
    hit_points: int
    bank: float
    bank_after: float
    # Over the best line-up of the squad as it stands, net of hits. `xi_gain`
    # is in the XI's expected points, captain included: what the gameweek is
    # expected to score. `gain` also counts the bench at `bench_weight`, which
    # is what the optimiser maximised; a bench-only upgrade moves it alone.
    # Neither is negative unless the squad is over the club cap.
    xi_gain: float
    gain: float
    bench_weight: float
    # Clubs the owned squad has more than MAX_PER_CLUB from, after a player
    # moved clubs. FPL lets the squad be kept, but not transferred from until
    # it is fixed, so the plan fixes it even at a cost against keeping.
    over_cap: dict[str, int]
    squad: Squad


def check_current(players, current, bank, free_transfers):
    """Why the current squad cannot be planned from, as readable lines."""
    found = []
    by_id = {p["player_id"]: p for p in players}
    if len(current) != 15:
        found.append(f"the squad has {len(current)} players, not 15")
    missing = sorted(set(current) - set(by_id))
    if missing:
        found.append(f"not in the entry: {missing}")
    known = [by_id[i] for i in current if i in by_id]
    if not missing and len(current) == 15:
        for position, quota in SQUAD.items():
            have = sum(p["position"] == position for p in known)
            if have != quota:
                found.append(f"{have} {position} in the squad, not {quota}")
    if any(price <= 0 for price in current.values()):
        found.append("every selling price must be above zero")
    if bank < 0:
        found.append(f"the bank is £{bank:.1f}m, below zero")
    # bool is an int in Python, so a JSON true would otherwise pass as 1.
    if (
        isinstance(free_transfers, bool)
        or not isinstance(free_transfers, int)
        or not 0 <= free_transfers <= MAX_FREE_TRANSFERS
    ):
        found.append(f"free transfers must be a whole number from 0 to {MAX_FREE_TRANSFERS}")
    return found


def best_transfers(
    players,
    current,
    bank,
    free_transfers,
    bench_weight=BENCH_WEIGHT,
    include_unavailable=False,
    time_limit=None,
):
    """The transfers from `current` that maximise expected points net of hits.

    `current` maps each of the fifteen owned players' ids to his selling
    price; `bank` is the money in the bank, in £m. The money to spend is the
    bank plus what the squad sells for, and a player kept costs his selling
    price: that is FPL's rule, as one budget constraint. Owned players stay
    eligible whatever their status, since a manager may keep anyone.
    `time_limit` is in seconds, for all the solves together (SolverTimeout).
    """
    check_positions(players)
    problems = check_current(players, current, bank, free_transfers)
    if problems:
        raise ValueError("cannot plan from this squad: " + "; ".join(problems))

    owned = set(current)
    pool = sorted(
        [p for p in eligible(players, include_unavailable) if p["player_id"] not in owned]
        + [p for p in players if p["player_id"] in owned],
        key=lambda p: p["player_id"],
    )
    cost = {
        p["player_id"]: (
            tenths(current[p["player_id"]]) if p["player_id"] in owned else tenths(p["price"])
        )
        for p in pool
    }
    budget = tenths(bank) + sum(tenths(price) for price in current.values())
    clubs = [p["team"] for p in pool if p["player_id"] in owned]
    over_cap = {
        club: clubs.count(club) for club in sorted(set(clubs)) if clubs.count(club) > MAX_PER_CLUB
    }

    deadline = _deadline(time_limit)
    kept_squad, kept_starters, kept_captain, kept = _solve(
        pool, cost, budget, bench_weight, owned, free_transfers, keep=True, deadline=deadline
    )
    kept_result = _squad(
        kept_squad, kept_starters, kept_captain, cost, budget / 10, current, verify=False
    )
    try:
        squad, starters, captain, value = _solve(
            pool, cost, budget, bench_weight, owned, free_transfers, deadline=deadline
        )
    except ValueError:
        if not over_cap:
            raise
        names = ", ".join(f"{count} from {club}" for club, count in over_cap.items())
        raise ValueError(
            f"the squad has {names}, over the {MAX_PER_CLUB}-per-club cap. FPL lets it be "
            "kept as it is, but any transfer must bring it within the cap, and no "
            "affordable set of transfers does. Keep the squad this week."
        ) from None
    result = _squad(squad, starters, captain, cost, budget / 10, current)

    by_id = {p["player_id"]: p for p in pool}
    new = {p["player_id"] for p in squad}
    transfers = []
    for position in POSITIONS:
        # Each position's quota is fixed, so its sales and buys pair off.
        # Shown weakest out against strongest in.
        sold = sorted(
            (by_id[i] for i in owned - new if by_id[i]["position"] == position),
            key=lambda p: (p["predicted_points"], p["player_id"]),
        )
        bought = sorted(
            (p for p in squad if p["player_id"] not in owned and p["position"] == position),
            key=lambda p: (-p["predicted_points"], p["player_id"]),
        )
        transfers += [
            Transfer(out=_pick(o, current), selling_price=current[o["player_id"]], into=_pick(b))
            for o, b in zip(sold, bought, strict=True)
        ]
    hits = max(0, len(transfers) - free_transfers)
    return Plan(
        transfers=transfers,
        free_transfers=free_transfers,
        hits=hits,
        hit_points=HIT * hits,
        bank=bank,
        bank_after=round(budget / 10 - result.cost, 1),
        xi_gain=round(result.expected_points - kept_result.expected_points - HIT * hits, 2),
        gain=(value - kept) / SCALE,
        bench_weight=bench_weight,
        over_cap=over_cap,
        squad=result,
    )


def violations(squad):
    """Every rule `squad` breaks, as readable lines. Shares nothing with the model."""
    found = []
    everyone = squad.xi + squad.bench
    ids = [p.player_id for p in everyone]
    if len(set(ids)) != 15:
        found.append(f"{len(set(ids))} distinct players, not 15")
    for position, quota in SQUAD.items():
        have = sum(p.position == position for p in everyone)
        if have != quota:
            found.append(f"{have} {position} in the squad, not {quota}")
    for club in {p.team for p in everyone}:
        have = sum(p.team == club for p in everyone)
        if have > MAX_PER_CLUB:
            found.append(f"{have} players from {club}, more than {MAX_PER_CLUB}")
    if tenths(squad.cost) > tenths(squad.budget):
        found.append(f"costs £{squad.cost:.1f}m, over £{squad.budget:.1f}m")
    if len(squad.xi) != XI_SIZE:
        found.append(f"{len(squad.xi)} starters, not {XI_SIZE}")
    shape = tuple(
        sum(p.position == position for p in squad.xi) for position in ("DEF", "MID", "FWD")
    )
    goalkeepers = sum(p.position == "GKP" for p in squad.xi)
    if goalkeepers != 1 or shape not in FORMATIONS:
        found.append(f"the XI is {goalkeepers} GKP and {shape}, not a legal formation")
    if squad.captain.player_id not in {p.player_id for p in squad.xi}:
        found.append("the captain does not start")
    if squad.vice_captain.player_id == squad.captain.player_id:
        found.append("the vice-captain is the captain")
    if squad.vice_captain.player_id not in {p.player_id for p in squad.xi}:
        found.append("the vice-captain does not start")
    return found


def describe(squad):
    """The squad as a readable block of text."""

    def line(p, mark=""):
        # A player owned is shown at what he sells for, marked *, so the
        # prices add up to the cost above.
        price = (
            f"£{p.selling_price:>5.1f}m*" if p.selling_price is not None else f"£{p.price:>5.1f}m "
        )
        return (
            f"  {p.position}  {p.web_name:<18}{p.team:<5}{price}  {p.predicted_points:>5.2f}{mark}"
        )

    lines = [
        f"Formation {squad.formation}, costing £{squad.cost:.1f}m of "
        f"£{squad.budget:.1f}m (£{squad.budget - squad.cost:.1f}m in the bank).",
        "",
    ]
    for p in squad.xi:
        mark = (
            " (C)"
            if p.player_id == squad.captain.player_id
            else " (V)"
            if p.player_id == squad.vice_captain.player_id
            else ""
        )
        lines.append(line(p, mark))
    lines += ["", "Bench, in order:"] + [line(p) for p in squad.bench]
    lines += [
        "",
        f"Expected points: {squad.expected_points:.2f}, captain included. "
        f"The bench adds {squad.bench_points:.2f} if it has to play.",
    ]
    if any(p.selling_price is not None for p in squad.xi + squad.bench):
        lines.append("* Owned: shown at what he sells for, which is what the cost counts.")
    return "\n".join(lines)


def describe_plan(plan):
    """A transfer plan as a readable block of text, then the squad it leaves."""
    lines = []
    if plan.over_cap:
        names = ", ".join(f"{count} from {club}" for club, count in plan.over_cap.items())
        lines.append(
            f"The squad has {names}, over the {MAX_PER_CLUB}-per-club cap. FPL lets "
            "it be kept as it is, but a transfer must fix it, so this plan does, "
            "and is measured against keeping it."
        )
    free = plan.free_transfers
    if not plan.transfers:
        if free >= MAX_FREE_TRANSFERS:
            lines.append(
                f"No transfer gains enough: keep the squad. With {free} free "
                f"transfers, the most FPL banks, one not used this week is lost."
            )
        elif free:
            lines.append(
                f"No transfer gains enough: keep the squad and roll the free "
                f"transfer{'s' if free > 1 else ''}."
            )
        else:
            lines.append("No transfer gains enough: keep the squad.")
    else:
        lines.append(f"Transfers, {len(plan.transfers)} with {free} free:")
        for t in plan.transfers:
            lines.append(
                f"  OUT {t.out.web_name} ({t.out.team} {t.out.position}, sells £"
                f"{t.selling_price:.1f}m, {t.out.predicted_points:.2f})  ->  IN "
                f"{t.into.web_name} ({t.into.team}, £{t.into.price:.1f}m, "
                f"{t.into.predicted_points:.2f})"
            )
        hit = f"-{plan.hit_points} for {plan.hits} beyond the free" if plan.hits else "none"
        lines.append(f"Hit: {hit}. Bank: £{plan.bank:.1f}m -> £{plan.bank_after:.1f}m.")
    lines.append(
        f"Expected gain over keeping the squad, net of hits: {plan.xi_gain:+.2f} "
        f"points in the XI, captain included ({plan.gain:+.2f} counting the bench "
        f"at {plan.bench_weight:g})."
    )
    return "\n".join(lines) + "\n\n" + describe(plan.squad)


def read_squad(path):
    """(current, bank, free_transfers) from a --squad JSON file."""
    try:
        data = json.loads(Path(path).read_text())
        ids = [int(p["player_id"]) for p in data["players"]]
        current = {int(p["player_id"]): float(p["selling_price"]) for p in data["players"]}
        bank, free_transfers = float(data["bank"]), data["free_transfers"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise SystemExit(
            f"{path} is not a squad file ({type(e).__name__}: {e}). Expected "
            '{"bank": 0.5, "free_transfers": 1, "players": '
            '[{"player_id": 16, "selling_price": 10.1}, ...]}'
        ) from None
    # Checked here, where the file's list is still a list: as a dict, a
    # repeated player would only show up as a squad one short.
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise SystemExit(f"{path} lists the same player more than once: {repeated}")
    if isinstance(free_transfers, bool) or not isinstance(free_transfers, int):
        raise SystemExit(f"{path}: free_transfers is {free_transfers!r}, not a whole number")
    return current, bank, free_transfers


def main(entry, budget, bench_weight, as_json, include_unavailable=False, squad_file=None):
    rows, faults = read_entry(entry)
    if faults:
        raise SystemExit(f"{entry} cannot be read:\n  " + "\n  ".join(faults[:10]))
    try:
        if squad_file is None:
            result = best_squad(
                rows,
                budget=budget,
                bench_weight=bench_weight,
                include_unavailable=include_unavailable,
            )
        else:
            current, bank, free_transfers = read_squad(squad_file)
            result = best_transfers(
                rows,
                current,
                bank,
                free_transfers,
                bench_weight=bench_weight,
                include_unavailable=include_unavailable,
            )
    except (ValueError, ImportError, SolverFailure) as e:
        raise SystemExit(str(e)) from None
    if as_json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(describe(result) if squad_file is None else describe_plan(result))
    return 0


def cli(argv=None):
    """`fpl-optimise`, or `python -m fpl.optimise`."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("entry", help="a prediction entry CSV, committed or under scratch/")
    ap.add_argument(
        "--budget",
        type=float,
        default=None,
        help=f"£m, for a squad from scratch (default: {BUDGET})",
    )
    ap.add_argument(
        "--squad",
        metavar="FILE",
        help="plan transfers from this squad instead: a JSON file of "
        "player ids and selling prices, the bank and free transfers",
    )
    ap.add_argument(
        "--bench-weight",
        type=float,
        default=BENCH_WEIGHT,
        help=f"what a bench player's points are worth (default: {BENCH_WEIGHT})",
    )
    ap.add_argument(
        "--include-unavailable",
        action="store_true",
        help="allow injured, suspended and not-available players (never "
        "those gone from the league)",
    )
    ap.add_argument("--json", action="store_true", help="print the squad as JSON")
    args = ap.parse_args(argv)
    if not 0 <= args.bench_weight <= 1:
        ap.error("--bench-weight must be between 0 and 1")
    if args.squad and args.budget is not None:
        ap.error(
            "--budget does not apply with --squad: the bank and selling prices "
            "set what there is to spend"
        )
    require_project_root()
    return main(
        args.entry,
        BUDGET if args.budget is None else args.budget,
        args.bench_weight,
        args.json,
        args.include_unavailable,
        args.squad,
    )


if __name__ == "__main__":
    sys.exit(cli())
