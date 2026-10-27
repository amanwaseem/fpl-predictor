"""Transfers planned over several gameweeks, by integer programming (#55, ADR 0022).

    fpl-plan --squad my-squad.json              # the next three gameweeks
    fpl-plan --squad my-squad.json --weeks 5
    fpl-plan                                    # a squad from scratch, then its transfers

fpl-optimise looks one gameweek ahead, so a transfer that pays off over the
next three but not this one is never made, and a free transfer is spent this
week when holding it would buy two next week. This plans the weeks together:
a squad, XI and captain for each, the transfers between them, and the free
transfers each week has.

## Where the points come from

Each gameweek's predictions are form-fixture-v1's, all from one snapshot: the
same rates, priced against each later fixture and counting its blanks and
doubles. They are inputs to the plan and nothing else: not written to
predictions/, not scored, and not a claim the log makes (ADR 0022). What a
snapshot knows is as of its deadline, so a player injured now is predicted as
injured for every week of the plan, and a price is the same throughout.

## The model

fpl-optimise's model once per week, with the weeks joined:

- each week's squad is legal on its own (fpl.optimise's quotas, club cap and
  budget), with a legal XI and a captain;
- a player in a week's squad but not the week before's is a transfer;
- a week has the free transfers left from the week before, plus one, banked
  up to MAX_FREE_TRANSFERS; they are used before any hit, and each transfer
  beyond them costs HIT points;
- week k's points count at DISCOUNT**k of the first week's, hits included, as
  later predictions are less sure.

From scratch, the first week's squad is chosen freely, as at the start of a
season or on a wildcard, and every later week has one free transfer more.

A player owned costs his selling price for as long as he is kept; once sold,
buying him back costs his price, as for anyone else. Chips are not played.
Every week's squad is checked against the rules by fpl.optimise.violations
before it is returned.

Writes nothing. Needs the HiGHS solver, in the optimise group.
"""

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass

from fpl.evaluation.xi import MAX_PER_CLUB, POSITIONS, SCALE, best_xi, check_positions
from fpl.ingest.snapshot import load_snapshot, player_summaries, resolve_target_gw, usable_rounds
from fpl.models import predict_fixture
from fpl.optimise import (
    BENCH_WEIGHT,
    BUDGET,
    HIT,
    MAX_FREE_TRANSFERS,
    SQUAD,
    SolverFailure,
    SolverTimeout,
    Squad,
    Transfer,
    _deadline,
    _pick,
    _solver,
    _squad,
    add_squad,
    check_current,
    describe,
    eligible,
    points,
    read_squad,
    shortfalls,
    tenths,
)
from fpl.paths import require_project_root

WEEKS = 3
MAX_WEEKS = 6
# A later week's points against the first's: predictions further out rest on
# the same form with more time for it to change. At least MIN_DISCOUNT, so
# the last of MAX_WEEKS still counts for something.
DISCOUNT = 0.9
MIN_DISCOUNT = 0.5
# Week weights in ten-thousandths, so every coefficient is an integer and the
# optimum exact, as in fpl.optimise; 0.5**5 is still 312 of them.
WEIGHT_SCALE = 10_000


@dataclass
class Week:
    gameweek: int
    transfers: list[Transfer]
    # None for a squad chosen from scratch, which has no transfers to count.
    free_transfers: int | None
    hits: int
    hit_points: int
    bank_after: float
    squad: Squad


@dataclass
class HorizonPlan:
    weeks: list[Week]
    # Fewer than asked for when the season ends first.
    weeks_requested: int
    discount: float
    bench_weight: float
    # What there is to spend: the bank and the squad's selling prices, or the
    # budget for a squad from scratch.
    money: float
    # The XI's expected points, captain included, net of hits, summed over
    # the weeks: for this plan, and for keeping the squad as it is.
    expected_points: float
    kept_points: float | None
    # The same as the plan weighs them, which is what it maximised: later
    # weeks discounted and the bench at bench_weight. The plan's is never
    # below keeping's.
    value: float
    kept_value: float | None


def horizon_predictions(snapshot_id=None, weeks=WEEKS):
    """(snapshot id, rounds left out, {gameweek: rows}) for the next `weeks` gameweeks.

    Every week is predicted from the rounds settled before the first, so no
    week knows more than its first deadline did. Rounds before it that have
    not settled are left out, and returned so the caller can say so.
    """
    snap, bootstrap, fixtures, players_dir = load_snapshot(snapshot_id)
    first, _ = resolve_target_gw(bootstrap["events"], None)
    usable, excluded = usable_rounds(bootstrap["events"], first)
    if not usable:
        raise SystemExit(
            f"No settled rounds before GW{first} in {snap.name}: nothing to plan from."
        )
    last = max(e["id"] for e in bootstrap["events"])
    histories, pasts = player_summaries(bootstrap, players_dir)
    by_week = {
        gw: predict_fixture.predict_rows(bootstrap, fixtures, histories, pasts, gw, usable)
        for gw in range(first, min(first + weeks, last + 1))
    }
    return snap.name, excluded, by_week


def best_plan(
    by_week,
    current=None,
    bank=0.0,
    free_transfers=1,
    budget=BUDGET,
    discount=DISCOUNT,
    bench_weight=BENCH_WEIGHT,
    include_unavailable=False,
    time_limit=None,
    weeks_requested=None,
):
    """The plan over `by_week` ({gameweek: rows}) maximising discounted points net of hits.

    With `current` ({player_id: selling_price}), plans from that squad, `bank`
    and `free_transfers`; without it, chooses the first week's squad freely
    within `budget`. Raises ValueError naming the cause when no plan exists,
    and SolverTimeout if none is proved optimal within `time_limit` seconds.
    """
    gameweeks = sorted(by_week)
    if not gameweeks:
        raise ValueError("no gameweeks to plan")
    if not MIN_DISCOUNT <= discount <= 1:
        raise ValueError(f"the discount must be from {MIN_DISCOUNT} to 1, not {discount}")
    first = by_week[gameweeks[0]]
    for rows in by_week.values():
        check_positions(rows)
    if current is not None:
        problems = check_current(first, current, bank, free_transfers)
        if problems:
            raise ValueError("cannot plan from this squad: " + "; ".join(problems))
        clubs = [p["team"] for p in first if p["player_id"] in current]
        over = sorted({c for c in clubs if clubs.count(c) > MAX_PER_CLUB})
        if over:
            raise ValueError(
                f"the squad is over the {MAX_PER_CLUB}-per-club cap ({', '.join(over)}) after "
                "a player moved clubs; plan one week with fpl-optimise --squad, which may keep it"
            )

    owned = set(current or ())
    pool = sorted(
        [p for p in eligible(first, include_unavailable) if p["player_id"] not in owned]
        + [p for p in first if p["player_id"] in owned],
        key=lambda p: p["player_id"],
    )
    if current is None:
        reasons = shortfalls(pool, budget)
        if reasons:
            raise ValueError("no legal squad: " + "; ".join(reasons))
        money = tenths(budget)
    else:
        money = tenths(bank) + sum(tenths(price) for price in current.values())
    price = {p["player_id"]: tenths(p["price"]) for p in pool}
    selling = {pid: tenths(value) for pid, value in (current or {}).items()}
    predicted = {
        gw: {r["player_id"]: r["predicted_points"] for r in rows} for gw, rows in by_week.items()
    }
    weights = [round(WEIGHT_SCALE * discount**k) for k in range(len(gameweeks))]

    solved = _solve(
        pool,
        price,
        selling,
        money,
        [predicted[gw] for gw in gameweeks],
        weights,
        bench_weight,
        free_transfers if current is not None else None,
        deadline=_deadline(time_limit),
    )

    # Free transfers and hits as FPL counts them, from the transfers made.
    weeks, before, held = [], owned, owned
    free = free_transfers if current is not None else None
    previous_cost, previous_sells_for = dict(selling), current
    value = 0.0
    for k, (gw, (squad, starters, captain)) in enumerate(zip(gameweeks, solved, strict=True)):
        worth = predicted[gw]
        new = {p["player_id"] for p in squad}
        held = held & new
        cost = {pid: selling[pid] if pid in held else price[pid] for pid in price}
        sells_for = {pid: current[pid] for pid in held} if current else None
        result = _squad(
            [dict(p, predicted_points=worth[p["player_id"]]) for p in squad],
            [dict(p, predicted_points=worth[p["player_id"]]) for p in starters],
            dict(captain, predicted_points=worth[captain["player_id"]]),
            cost,
            money / 10,
            sells_for,
        )
        by_id = {p["player_id"]: dict(p, predicted_points=worth[p["player_id"]]) for p in pool}
        transfers = []
        # A first squad chosen from scratch is not transfers.
        for position in POSITIONS if free is not None else ():
            sold = sorted(
                (by_id[i] for i in before - new if by_id[i]["position"] == position),
                key=lambda p: (p["predicted_points"], p["player_id"]),
            )
            bought = sorted(
                (by_id[i] for i in new - before if by_id[i]["position"] == position),
                key=lambda p: (-p["predicted_points"], p["player_id"]),
            )
            transfers += [
                Transfer(
                    out=_pick(o, previous_sells_for),
                    selling_price=previous_cost[o["player_id"]] / 10,
                    into=_pick(b),
                )
                for o, b in zip(sold, bought, strict=True)
            ]
        hits = 0 if free is None else max(0, len(transfers) - free)
        weeks.append(
            Week(
                gameweek=gw,
                transfers=transfers,
                free_transfers=free,
                hits=hits,
                hit_points=HIT * hits,
                bank_after=round((money - sum(cost[pid] for pid in new)) / 10, 1),
                squad=result,
            )
        )
        value += (
            weights[k]
            / WEIGHT_SCALE
            * (result.expected_points + bench_weight * result.bench_points - HIT * hits)
        )
        free = 1 if free is None else min(MAX_FREE_TRANSFERS, max(0, free - len(transfers)) + 1)
        before, previous_cost, previous_sells_for = new, cost, sells_for

    kept_points = kept_value = None
    if current is not None:
        kept_points = kept_value = 0.0
        for k, gw in enumerate(gameweeks):
            xi, bench_points = _kept_week(first, current, predicted[gw])
            kept_points += xi
            kept_value += weights[k] / WEIGHT_SCALE * (xi + bench_weight * bench_points)

    return HorizonPlan(
        weeks=weeks,
        weeks_requested=weeks_requested or len(gameweeks),
        discount=discount,
        bench_weight=bench_weight,
        money=money / 10,
        expected_points=round(sum(w.squad.expected_points - w.hit_points for w in weeks), 2),
        kept_points=None if kept_points is None else round(kept_points, 2),
        value=round(value, 2),
        kept_value=None if kept_value is None else round(kept_value, 2),
    )


def _kept_week(first, current, worth):
    """(XI points with the captain's twice, bench points) for the owned squad in one week.

    The best XI of a fixed squad holds its best player, so he is the captain,
    and the rest is the bench: no solver needed.
    """
    squad = [
        dict(p, predicted_points=worth[p["player_id"]]) for p in first if p["player_id"] in current
    ]
    total, xi, _ = best_xi(squad, lambda p: p["predicted_points"])
    captain = max(points(p["predicted_points"]) for p in xi)
    everyone = sum(points(p["predicted_points"]) for p in squad)
    starting = sum(points(p["predicted_points"]) for p in xi)
    return (starting + captain) / SCALE, (everyone - starting) / SCALE


def _solve(
    pool, price, selling, money, worth_by_week, weights, bench_weight, free_transfers, deadline=None
):
    """One model over every week: [(squad, starters, captain)], one a week.

    `price` and `selling` map player ids to tenths of a million; `selling`
    holds the owned squad's selling prices, and `free_transfers` is None for
    a first squad chosen from scratch. Each week is fpl.optimise.add_squad;
    the weeks are joined by what each costs, the transfers between them and
    the free transfers carried. Solved as fpl.optimise._solve is: points
    first with a zero gap, then ties broken toward fewer transfers and lower
    player ids with the points held, the first answer standing if that runs
    out of time.
    """
    highspy = _solver()
    n = len(pool)
    owned = [i for i in range(n) if pool[i]["player_id"] in selling]
    model = highspy.Highs()
    model.silent()
    model.setOptionValue("mip_rel_gap", 0.0)
    model.setOptionValue("mip_abs_gap", 0.5)

    def limit_time():
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SolverTimeout("the solver's time limit was reached")
            model.setOptionValue("time_limit", remaining)

    weeks = []
    value = 0
    transfer_count = 0
    for k, worth in enumerate(worth_by_week):
        squad, start, captain, week_value = add_squad(
            model, pool, [points(worth[p["player_id"]]) for p in pool], bench_weight
        )
        # An owned player is held while he has been in every squad so far,
        # and costs his selling price only while held: held = held last week
        # and in this squad.
        held = {}
        for i in owned:
            held[i] = model.addBinary()
            model.addConstr(held[i] <= squad[i])
            if k == 0:
                model.addConstr(held[i] >= squad[i])
            else:
                last = weeks[-1]["held"][i]
                model.addConstr(held[i] <= last)
                model.addConstr(held[i] >= last + squad[i] - 1)
        spend = model.qsum(
            price[pool[i]["player_id"]] * squad[i] for i in range(n) if i not in held
        ) + model.qsum(
            selling[pool[i]["player_id"]] * held[i]
            + price[pool[i]["player_id"]] * (squad[i] - held[i])
            for i in owned
        )
        model.addConstr(spend <= money)

        free = used = None
        if k > 0 or free_transfers is not None:
            if k == 0:
                bought = model.qsum(squad[i] for i in range(n) if i not in held)
                free = free_transfers
            else:
                # In this week's squad and not last week's. Continuous is
                # enough: it is held at its lower bound, which is 0 or 1.
                moved = model.addVariables(n, lb=0, ub=1)
                for i in range(n):
                    model.addConstr(moved[i] >= squad[i] - weeks[-1]["squad"][i])
                bought = model.qsum(moved[i] for i in range(n))
                last = weeks[-1]
                if last["free"] is None:
                    # The week after a squad chosen from scratch.
                    free = 1
                else:
                    # What was left last week, plus one, banked up to the most.
                    free = model.addIntegral(lb=1, ub=MAX_FREE_TRANSFERS)
                    model.addConstr(free <= last["free"] - last["used"] + 1)
            used = model.addIntegral(lb=0, ub=MAX_FREE_TRANSFERS)
            hit = model.addIntegral(lb=0, ub=sum(SQUAD.values()))
            # Free transfers are used before any hit is taken: a hit means
            # every free transfer this week was used.
            any_hit = model.addBinary()
            model.addConstr(used <= free)
            model.addConstr(hit <= sum(SQUAD.values()) * any_hit)
            model.addConstr(used >= free - MAX_FREE_TRANSFERS * (1 - any_hit))
            model.addConstr(bought == used + hit)
            week_value = week_value - HIT * SCALE * hit
            transfer_count = transfer_count + bought
        weeks.append(
            {
                "squad": squad,
                "start": start,
                "captain": captain,
                "held": held,
                "free": free,
                "used": used,
            }
        )
        value = value + weights[k] * week_value

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
            f"no legal plan within £{money / 10:.1f}m: each rule can be met alone, but the "
            "budget and the three-per-club cap leave no squad"
        )
    best = round(model.getObjectiveValue())

    def read():
        out = []
        for week in weeks:
            squad, starters, captains = (
                [pool[i] for i, v in enumerate(model.vals(week[name])) if v > 0.5]
                for name in ("squad", "start", "captain")
            )
            out.append((squad, starters, captains[0]))
        return out

    solution = read()
    # As fpl.optimise: hold the points, then fewer transfers outweigh any
    # difference in rank, which is at most 27 chosen variables a week each
    # ranked below n.
    model.addConstr(value >= best - 0.5)
    rank = model.qsum(
        i * (week["squad"][i] + week["start"][i] + week["captain"][i])
        for week in weeks
        for i in range(n)
    )
    try:
        limit_time()
        model.minimize((27 * n * len(weeks) + 1) * transfer_count + rank)
        if model.getModelStatus() == highspy.HighsModelStatus.kOptimal:
            solution = read()
    except SolverTimeout:
        pass
    return solution


def describe_plan(plan):
    """The plan as readable text: each week's transfers, then the first week's squad."""
    lines = []
    if len(plan.weeks) < plan.weeks_requested:
        lines += [
            f"The season ends first: {len(plan.weeks)} of the {plan.weeks_requested} "
            "gameweeks asked for are left.",
            "",
        ]
    for week in plan.weeks:
        head = f"GW{week.gameweek}"
        if week.free_transfers is None:
            lines.append(f"{head}: the squad below, chosen from scratch.")
        elif not week.transfers:
            lines.append(f"{head}: no transfers ({week.free_transfers} free).")
        else:
            hit = f", -{week.hit_points}" if week.hits else ""
            lines.append(
                f"{head}: {len(week.transfers)} transfer(s), {week.free_transfers} free{hit}."
            )
            for t in week.transfers:
                lines.append(
                    f"  OUT {t.out.web_name} ({t.out.team} {t.out.position}, sells £"
                    f"{t.selling_price:.1f}m)  ->  IN {t.into.web_name} ({t.into.team}, £"
                    f"{t.into.price:.1f}m)"
                )
        lines.append(
            f"  XI {week.squad.expected_points:.2f} expected, captain "
            f"{week.squad.captain.web_name}; £{week.bank_after:.1f}m in the bank."
        )
    lines.append("")
    total = (
        f"Over {len(plan.weeks)} gameweeks: {plan.expected_points:.2f} expected in the XI, "
        "captain included, net of hits"
    )
    if plan.kept_points is not None:
        total += f", against {plan.kept_points:.2f} keeping the squad"
    lines.append(total + ".")
    weighed = (
        f"As the plan weighs them, each week at {plan.discount:g} of the one before and the "
        f"bench at {plan.bench_weight:g}: {plan.value:.2f}"
    )
    if plan.kept_value is not None:
        weighed += f", against {plan.kept_value:.2f}"
    lines.append(weighed + ".")
    lines.append(
        "Only the first week's transfers are a decision now; the rest are the plan as it "
        "stands, for the next snapshot to revisit."
    )
    return "\n".join(lines) + "\n\n" + describe(plan.weeks[0].squad)


def main(weeks, squad_file, budget, discount, bench_weight, as_json, include_unavailable):
    snapshot, excluded, by_week = horizon_predictions(weeks=weeks)
    try:
        if squad_file is None:
            plan = best_plan(
                by_week,
                budget=budget,
                discount=discount,
                bench_weight=bench_weight,
                include_unavailable=include_unavailable,
                weeks_requested=weeks,
            )
        else:
            current, bank, free_transfers = read_squad(squad_file)
            plan = best_plan(
                by_week,
                current,
                bank,
                free_transfers,
                discount=discount,
                bench_weight=bench_weight,
                include_unavailable=include_unavailable,
                weeks_requested=weeks,
            )
    except (ValueError, ImportError, SolverFailure) as e:
        raise SystemExit(str(e)) from None
    if as_json:
        print(json.dumps({"snapshot_id": snapshot, "excluded": excluded} | asdict(plan), indent=2))
    else:
        print(f"snapshot:  {snapshot} (form-fixture-v1, not logged)")
        if excluded:
            rounds = ", ".join(f"GW{gw}" for gw in excluded)
            print(f"excluded:  {rounds}  (results not final; take a snapshot once they settle)")
        print()
        print(describe_plan(plan))
    return 0


def cli(argv=None):
    """`fpl-plan`, or `python -m fpl.plan`."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--weeks",
        type=int,
        default=WEEKS,
        help=f"gameweeks to plan, from the next (default: {WEEKS}, at most {MAX_WEEKS})",
    )
    ap.add_argument(
        "--squad",
        metavar="FILE",
        help="plan from this squad: fpl-optimise's --squad file. Without it, the "
        "first week's squad is chosen from scratch",
    )
    ap.add_argument(
        "--budget",
        type=float,
        default=None,
        help=f"£m, for a squad from scratch (default: {BUDGET})",
    )
    ap.add_argument(
        "--discount",
        type=float,
        default=DISCOUNT,
        help=f"each later week's weight against the one before (default: {DISCOUNT})",
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
        help="allow injured, suspended and not-available players",
    )
    ap.add_argument("--json", action="store_true", help="print the plan as JSON")
    args = ap.parse_args(argv)
    if not 1 <= args.weeks <= MAX_WEEKS:
        ap.error(f"--weeks must be from 1 to {MAX_WEEKS}")
    if not MIN_DISCOUNT <= args.discount <= 1:
        ap.error(f"--discount must be from {MIN_DISCOUNT} to 1")
    if not 0 <= args.bench_weight <= 1:
        ap.error("--bench-weight must be between 0 and 1")
    if args.squad and args.budget is not None:
        ap.error("--budget does not apply with --squad: the bank and selling prices set it")
    require_project_root()
    return main(
        args.weeks,
        args.squad,
        BUDGET if args.budget is None else args.budget,
        args.discount,
        args.bench_weight,
        args.json,
        args.include_unavailable,
    )


if __name__ == "__main__":
    sys.exit(cli())
