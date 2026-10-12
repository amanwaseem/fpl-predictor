"""Models that exist to be backtested, never logged.

A candidate isolates one idea so the backtest can say whether it earns its
place before a logged model depends on it. Nothing here writes to
predictions/, and nothing here has a log entry to be consistent with: when an
idea is judged, it moves into a logged model (built from fpl.features, not
from this file) and the candidate is deleted.

baseline-prior (#30): baseline-v1 with last season's points-per-90 as the
prior in place of the positional one. Minutes, window, decay and fixture count
are baseline-v1's, so a difference on the backtest is the prior's doing.

Comparators for form-fixture-v1 (#32). Once an idea is judged it moves into
the logged model, but the model still has to be judged against what it
replaced. These run fpl.predict_fixture's own code with one piece swapped, so
they cannot drift from it:

- baseline-fixture: the positional prior instead of last season (#31 alone).
- baseline-fdr: that, on FPL's difficulty instead of the team ratings.
- baseline-prior-fdr: last season's prior, on FPL's difficulty — the
  comparator the ratings have to beat once the prior is in.

price-prior (#49): the form-fixture-v2 candidate, with the flat positional
prior moved by price for players without a usable last season. Per position,
each part of last season's points per 90 (total, attacking returns, clean
sheets) is fitted against price over the players who do have one, and the
slope moves the flat prior by how far a player's price is from the mean of
the players it applies to. Price is public at every deadline, and in the backtest's
view it is the player's value in his last round before the target, so the
fit sees nothing later. flat-prior-weight is its control: the same weight
on the flat prior, with no price.
"""

from fpl.features import (
    PLAYER_PRIOR_HALF_LIFE_MINUTES,
    PLAYER_PRIOR_MINUTES,
    POSITION_PRIOR_MINUTES,
    expected_minutes,
    player_prior,
    player_prior_parts,
    previous_season,
    prior,
    recent_history,
    season_minutes,
    shrunk_pp90,
)
from fpl.predict_fixture import predict_rows as fixture_rows
from fpl.snapshot import fixture_counts, usable_rounds


def baseline_prior_rows(bootstrap, fixtures, histories, target_gw, pasts,
                        player_prior_minutes=PLAYER_PRIOR_MINUTES, floor=None,
                        xg_weight=None):
    """Log-shaped rows, as baseline-v1's predict_rows, with a player prior.

    `pasts` maps player id to history_past rows. The prior's weight, floor and
    xG weight are exposed so the backtest can tune them held out
    (fpl.backtest.GRIDS); a logged model fixes them. None means features'
    constant.
    """
    usable, _ = usable_rounds(bootstrap["events"], target_gw)
    season = previous_season(bootstrap["events"])
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    positions = {p["id"]: p["singular_name_short"] for p in bootstrap["element_types"]}
    counts = fixture_counts(fixtures, target_gw)

    rows = []
    for player in bootstrap["elements"]:
        pid = player["id"]
        if pid not in histories:
            continue
        history = histories[pid]
        position = positions.get(player["element_type"], "UNK")
        n_fix = counts.get(player["team"], 0)

        predicted = exp_min = pp90 = 0.0
        recent = recent_history(history, target_gw, usable)
        if n_fix and recent:
            exp_min = expected_minutes(recent, player)
            prior_pp90, prior_minutes = prior(
                position,
                player_prior(pasts.get(pid, []), season, position, xg_weight, floor),
                season_minutes(history, target_gw, usable),
                player_prior_minutes,
            )
            pp90 = shrunk_pp90(sum(r["points"] for r in recent),
                               sum(r["minutes"] for r in recent),
                               prior_pp90, prior_minutes)
            predicted = pp90 * (exp_min / 90.0) * n_fix

        rows.append({
            "gameweek": target_gw,
            "player_id": pid,
            "web_name": player["web_name"],
            "team": teams.get(player["team"], "UNK"),
            "position": position,
            "price": player["now_cost"] / 10.0,
            "predicted_points": round(predicted, 2),
            "expected_minutes": round(exp_min, 1),
            "points_per_90": round(pp90, 2),
            "n_fixtures": n_fix,
            "status": player.get("status", ""),
        })

    rows.sort(key=lambda r: (-r["predicted_points"], r["player_id"]))
    return rows


def baseline_fixture_rows(bootstrap, fixtures, histories, target_gw, pasts, **options):
    """form-fixture-v1's code with pieces swapped, for comparison.

    Runs fpl.predict_fixture.predict_rows, so a comparator differs from the
    logged model only in what it is asked to swap: `last_season=False` for
    the positional prior (baseline-fixture), `strength="fdr"` for FPL's
    difficulty in place of the ratings (baseline-fdr, baseline-prior-fdr).
    Defaults here are the positional prior, as baseline-fixture was built.
    """
    usable, _ = usable_rounds(bootstrap["events"], target_gw)
    options.setdefault("last_season", False)
    return fixture_rows(bootstrap, fixtures, histories, pasts, target_gw, usable, **options)


# The fewest players a position's slope is fitted on. Below it the position
# keeps its flat prior: two points make a line, and not a believable one.
PRICE_FIT_MIN_PLAYERS = 5


def slope(points):
    """Least-squares slope of y on x through (x, y) points. 0 if x never varies."""
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    spread = sum((x - mean_x) ** 2 for x, _ in points)
    if not spread:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / spread


def price_fit(bootstrap, pasts, floor=None, xg_weight=None):
    """(slopes, centres) for the price prior, per position, in one pass.

    slopes: the points per 90 a £1m step in price is worth for (total,
    attack, clean sheet), fitted over the players with a usable last season,
    on his price now and his parts from last season. `floor` and `xg_weight`
    are the model's, so the fit and the model agree on who has one. A
    position with too few such players is left out.

    centres: the mean price of the players without one, the players the
    prior is applied to. The flat prior is their prior, so at their mean
    price it is kept as it is.
    """
    season = previous_season(bootstrap["events"])
    positions = {p["id"]: p["singular_name_short"] for p in bootstrap["element_types"]}
    fitted, unfitted = {}, {}
    for player in bootstrap["elements"]:
        position = positions.get(player["element_type"], "UNK")
        price = player["now_cost"] / 10.0
        parts = player_prior_parts(pasts.get(player["id"], []), season, position,
                                   xg_weight, floor)
        if parts is None:
            unfitted.setdefault(position, []).append(price)
        else:
            fitted.setdefault(position, []).append(
                (price, (parts["total"], parts["attack"], parts["clean_sheet"])))
    slopes = {position: tuple(slope([(price, parts[i]) for price, parts in rows])
                              for i in range(3))
              for position, rows in fitted.items()
              if len(rows) >= PRICE_FIT_MIN_PLAYERS and position in unfitted}
    centres = {position: sum(prices) / len(prices) for position, prices in unfitted.items()}
    return slopes, centres


def faded(prior_minutes, minutes_this_season):
    """A prior's weight, fading as this season's minutes accumulate.

    The same fade as the player prior's (fpl.features.prior): a weight chosen
    on a few early rounds would otherwise outweigh a regular's form all
    season.
    """
    return prior_minutes * PLAYER_PRIOR_HALF_LIFE_MINUTES / (
        PLAYER_PRIOR_HALF_LIFE_MINUTES + minutes_this_season)


def price_prior(slopes, centres, prior_minutes):
    """A positional_prior for fpl.predict_fixture: the flat prior, moved by price.

    Attack, clean sheet and the rest each move by their slope times how far
    his price is from the mean of the players this applies to, and the total
    is their sum. Each is floored at zero, the rest included, so the parts
    can never add up to more than the total. Only the slope is borrowed from
    the fit: the line is fitted on regulars, who earn more per 90 than the
    squad players it is applied to, and read whole it lifted their level as
    well as reordering them (held out over GW2-5, MAE 1.1228 against 1.1132
    without it).
    """
    def prior_for(player, position, priors, _, minutes_this_season):
        weight = faded(prior_minutes, minutes_this_season)
        if position not in slopes:
            return priors, weight
        offset = player["now_cost"] / 10.0 - centres[position]
        total, attack, clean_sheet = priors
        b_total, b_attack, b_clean_sheet = slopes[position]
        attack = max(0.0, attack + b_attack * offset)
        clean_sheet = max(0.0, clean_sheet + b_clean_sheet * offset)
        rest = max(0.0, priors[0] - priors[1] - priors[2]
                   + (b_total - b_attack - b_clean_sheet) * offset)
        return (attack + clean_sheet + rest, attack, clean_sheet), weight
    return prior_for


def price_prior_rows(bootstrap, fixtures, histories, target_gw, pasts=None,
                     price_prior_minutes=POSITION_PRIOR_MINUTES, **options):
    """price-prior: the form-fixture-v2 candidate, with the price prior (#49)."""
    pasts = pasts or {}
    usable, _ = usable_rounds(bootstrap["events"], target_gw)
    slopes, centres = price_fit(bootstrap, pasts, options.get("floor"),
                                options.get("xg_weight"))
    return fixture_rows(bootstrap, fixtures, histories, pasts, target_gw, usable,
                        goalkeeping=True,
                        positional_prior=price_prior(slopes, centres, price_prior_minutes),
                        **options)


def flat_prior_rows(bootstrap, fixtures, histories, target_gw, pasts=None,
                    price_prior_minutes=POSITION_PRIOR_MINUTES, **options):
    """flat-prior-weight: price-prior's control, the flat prior at its weight.

    price-prior changes two things for a player without a usable last season:
    what his prior is, and how much it weighs. This changes only the second,
    faded the same way, so the difference between them is what price itself
    is worth.
    """
    usable, _ = usable_rounds(bootstrap["events"], target_gw)
    return fixture_rows(
        bootstrap, fixtures, histories, pasts or {}, target_gw, usable, goalkeeping=True,
        positional_prior=lambda _, __, priors, ___, minutes: (
            priors, faded(price_prior_minutes, minutes)),
        **options)
