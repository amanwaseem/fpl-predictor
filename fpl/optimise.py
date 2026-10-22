"""The best legal squad for one gameweek, by integer programming (#53, ADR 0010).

    fpl-optimise predictions/gw06_form-fixture-v1.csv
    fpl-optimise scratch/gw08_baseline-v1.csv --budget 98.5 --json

Given one entry's predictions, picks the fifteen players, the starting eleven
and the captain that maximise expected points:

- £100.0m budget, on the entry's `price` column;
- 2 GKP, 5 DEF, 5 MID, 3 FWD, and at most three players from any club;
- a legal XI, with the formations and the club cap taken from fpl.xi, so the
  optimiser and the scoring harness cannot disagree about what is legal;
- the captain scores double;
- each bench player counts at BENCH_WEIGHT of their points.

Squad, XI and captain are chosen together in one model rather than in turn:
the best squad depends on who would start, and greedy picking is wrong once
the budget and the club cap interact (ADR 0010).

## Exact, and the same every time

Points are counted in hundredths and prices in tenths, so every coefficient is
an integer and the optimum is checked to the last hundredth: the solver's gap
is set to zero, not left at its default of 0.01%. Ties, which are common among
cheap players predicted 0.00, go to lower player ids. A tie-break term is
scaled below one hundredth of a point, so it can never trade away real points.
The same entry therefore gives the same squad on any machine.

Every solution is checked against the rules, by code that shares nothing with
the model, before it is returned. Writes nothing; reads the entry it is given.
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass

import highspy

from fpl.log import read_entry
from fpl.paths import require_project_root
from fpl.xi import FORMATIONS, MAX_PER_CLUB, POSITIONS, XI_SIZE

BUDGET = 100.0
SQUAD = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
# A bench player scores only when a starter does not play. Weighting the bench
# at a tenth makes the optimiser prefer a bench that might score over one that
# will not, at the same price, without letting bench points buy a worse XI.
BENCH_WEIGHT = 0.1

# Starters per position, from fpl.xi's formations rather than a second copy:
# 1 GKP, and each outfield position between its fewest and most in any
# formation. With eleven starters in all, these bounds admit exactly the
# formations fpl.xi lists; tests/test_optimise.py checks that.
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


def tenths(value):
    return round(value * 10)


def hundredths(value):
    return round(value * 100)


def best_squad(players, budget=BUDGET, bench_weight=BENCH_WEIGHT):
    """The squad, XI and captain maximising expected points, as a Squad.

    `players` are dicts with player_id, web_name, team, position, price and
    predicted_points, as fpl.log.read_entry returns them. Raises ValueError if
    no legal squad fits the budget.
    """
    unknown = sorted({p["position"] for p in players} - set(POSITIONS))
    if unknown:
        raise ValueError(f"unknown position(s): {unknown}")
    pool = sorted(players, key=lambda p: p["player_id"])
    n = len(pool)

    model = highspy.Highs()
    model.silent()
    # Exact: the objective is integral, so a gap below one unit is optimality.
    model.setOptionValue("mip_rel_gap", 0.0)
    model.setOptionValue("mip_abs_gap", 0.5)
    squad = model.addBinaries(n)
    start = model.addBinaries(n)
    captain = model.addBinaries(n)

    for i in range(n):
        model.addConstr(start[i] <= squad[i])
        model.addConstr(captain[i] <= start[i])
    model.addConstr(sum(captain[i] for i in range(n)) == 1)
    model.addConstr(sum(start[i] for i in range(n)) == XI_SIZE)
    for position in POSITIONS:
        members = [i for i in range(n) if pool[i]["position"] == position]
        model.addConstr(sum(squad[i] for i in members) == SQUAD[position])
        low, high = XI_BOUNDS[position]
        model.addConstr(sum(start[i] for i in members) >= low)
        model.addConstr(sum(start[i] for i in members) <= high)
    for club in sorted({p["team"] for p in pool}):
        model.addConstr(sum(squad[i] for i in range(n) if pool[i]["team"] == club)
                        <= MAX_PER_CLUB)
    model.addConstr(sum(tenths(pool[i]["price"]) * squad[i] for i in range(n))
                    <= tenths(budget))

    # Expected points in hundredths: a starter's, again for the captain, and
    # bench_weight of a bench player's.
    points = [hundredths(p["predicted_points"]) for p in pool]
    bench = [round(points[i] * bench_weight) for i in range(n)]
    value = sum(points[i] * start[i] + points[i] * captain[i]
                + bench[i] * (squad[i] - start[i]) for i in range(n))
    # Tie-break toward lower ids. At most 27 chosen variables (15 + 11 + 1),
    # each ranked below n, so the whole tie-break stays under one unit of
    # `value` scaled by 27n: it can only choose between equal points.
    rank = sum(i * (squad[i] + start[i] + captain[i]) for i in range(n))
    model.maximize(27 * n * value - rank)

    if model.getModelStatus() != highspy.HighsModelStatus.kOptimal:
        cheapest = sorted(p["price"] for p in pool)
        raise ValueError(
            f"no legal squad within £{budget:.1f}m: the solver reports "
            f"{model.modelStatusToString(model.getModelStatus())}. The cheapest "
            f"fifteen players alone cost £{sum(cheapest[:15]):.1f}m, before the "
            "position and club rules.")

    def chosen(variables):
        return [pool[i] for i, v in enumerate(model.vals(variables)) if v > 0.5]

    return _squad(chosen(squad), chosen(start), chosen(captain)[0], budget, bench_weight)


def _pick(player):
    return Pick(**{field: player[field] for field in Pick.__dataclass_fields__})


def _squad(squad, starters, captain, budget, bench_weight):
    order = {position: i for i, position in enumerate(POSITIONS)}
    xi = sorted(starters, key=lambda p: (order[p["position"]], -p["predicted_points"],
                                         p["player_id"]))
    starting = {p["player_id"] for p in xi}
    # FPL's bench order: the reserve goalkeeper first, then outfield players
    # in the order they would come on, best first.
    bench = sorted((p for p in squad if p["player_id"] not in starting),
                   key=lambda p: (p["position"] != "GKP", -p["predicted_points"],
                                  p["player_id"]))
    vice = max((p for p in xi if p["player_id"] != captain["player_id"]),
               key=lambda p: (p["predicted_points"], -p["player_id"]))
    counts = [sum(p["position"] == position for p in xi) for position in ("DEF", "MID", "FWD")]
    result = Squad(
        xi=[_pick(p) for p in xi], bench=[_pick(p) for p in bench],
        captain=_pick(captain), vice_captain=_pick(vice),
        formation="-".join(str(c) for c in counts),
        cost=sum(tenths(p["price"]) for p in squad) / 10, budget=budget,
        expected_points=(sum(hundredths(p["predicted_points"]) for p in xi)
                         + hundredths(captain["predicted_points"])) / 100,
        bench_points=sum(hundredths(p["predicted_points"]) for p in bench) / 100,
    )
    broken = violations(result)
    if broken:
        raise AssertionError("the solver returned an illegal squad: " + "; ".join(broken))
    return result


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
    shape = tuple(sum(p.position == position for p in squad.xi)
                  for position in ("DEF", "MID", "FWD"))
    goalkeepers = sum(p.position == "GKP" for p in squad.xi)
    if goalkeepers != 1 or shape not in FORMATIONS:
        found.append(f"the XI is {goalkeepers} GKP and {shape}, not a legal formation")
    if squad.captain.player_id not in {p.player_id for p in squad.xi}:
        found.append("the captain does not start")
    if squad.vice_captain.player_id == squad.captain.player_id:
        found.append("the vice-captain is the captain")
    return found


def describe(squad):
    """The squad as a readable block of text."""
    def line(p, mark=""):
        return (f"  {p.position}  {p.web_name:<18}{p.team:<5}£{p.price:>5.1f}m"
                f"  {p.predicted_points:>5.2f}{mark}")
    lines = [f"Formation {squad.formation}, costing £{squad.cost:.1f}m of "
             f"£{squad.budget:.1f}m (£{squad.budget - squad.cost:.1f}m in the bank).", ""]
    for p in squad.xi:
        mark = (" (C)" if p.player_id == squad.captain.player_id
                else " (V)" if p.player_id == squad.vice_captain.player_id else "")
        lines.append(line(p, mark))
    lines += ["", "Bench, in order:"] + [line(p) for p in squad.bench]
    lines += ["", f"Expected points: {squad.expected_points:.2f}, captain included. "
                  f"The bench adds {squad.bench_points:.2f} if it has to play."]
    return "\n".join(lines)


def main(entry, budget, bench_weight, as_json):
    rows, faults = read_entry(entry)
    if faults:
        raise SystemExit(f"{entry} cannot be read:\n  " + "\n  ".join(faults[:10]))
    try:
        squad = best_squad(rows, budget=budget, bench_weight=bench_weight)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    print(json.dumps(asdict(squad), indent=2) if as_json else describe(squad))
    return 0


def cli(argv=None):
    """`fpl-optimise`, or `python -m fpl.optimise`."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("entry", help="a prediction entry CSV, committed or under scratch/")
    ap.add_argument("--budget", type=float, default=BUDGET, help="£m (default: 100.0)")
    ap.add_argument("--bench-weight", type=float, default=BENCH_WEIGHT,
                    help=f"what a bench player's points are worth (default: {BENCH_WEIGHT})")
    ap.add_argument("--json", action="store_true", help="print the squad as JSON")
    args = ap.parse_args(argv)
    if not 0 <= args.bench_weight <= 1:
        ap.error("--bench-weight must be between 0 and 1")
    require_project_root()
    return main(args.entry, args.budget, args.bench_weight, args.json)


if __name__ == "__main__":
    sys.exit(cli())
