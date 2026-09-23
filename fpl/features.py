"""Turning snapshot rows into model inputs.

Shared by every model that reasons from recent form. The window and the decay
are here rather than in any one model because two models disagreeing about
what "recent form" means would make their predictions incomparable, and the
log's whole value is that entries can be compared.
"""

# Exponential decay over recent gameweeks, most recent first.
# Roughly a half-life of two gameweeks.
DECAY = 0.7
LOOKBACK = 5


def recent_history(history, target_gw, usable):
    """Recent gameweeks, most recent first, aggregated per round.

    Doubles produce two rows for one round, so sum them and record how many
    fixtures the round held — minutes have to be read per fixture later, while
    points stay summed. Only rounds strictly
    before the target are used — never look at the gameweek being predicted —
    and only rounds whose results are final. The target check is redundant with
    `usable` by construction and kept anyway: a leak here invalidates the whole
    track record.
    """
    by_round = {}
    for h in history:
        rnd = h.get("round")
        if rnd is None or rnd >= target_gw or rnd not in usable:
            continue
        entry = by_round.setdefault(rnd, {"minutes": 0, "points": 0, "fixtures": 0})
        entry["minutes"] += h.get("minutes", 0) or 0
        entry["points"] += h.get("total_points", 0) or 0
        entry["fixtures"] += 1

    ordered = sorted(by_round.items(), key=lambda kv: kv[0], reverse=True)
    return [v for _, v in ordered[:LOOKBACK]]


def recent_rows(history, target_gw, usable):
    """The raw history rows behind recent_history: same rounds, same rules.

    For a model that needs more than minutes and points from the window —
    goals, assists, clean sheets — without choosing its rounds differently.
    """
    rounds = {h["round"] for h in history
              if h.get("round") is not None and h["round"] < target_gw
              and h["round"] in usable}
    kept = set(sorted(rounds, reverse=True)[:LOOKBACK])
    return [h for h in history if h.get("round") in kept]


def weighted(values):
    """Exponentially decayed mean. Input is most-recent-first."""
    if not values:
        return 0.0, 0.0
    total = weight_sum = 0.0
    for i, v in enumerate(values):
        w = DECAY ** i
        total += v * w
        weight_sum += w
    return total / weight_sum, weight_sum


def availability(player):
    """Fraction of normal minutes expected, from injury/suspension flags.

    status: a=available, d=doubtful, i=injured, s=suspended, u=unavailable
    """
    chance = player.get("chance_of_playing_next_round")
    if chance is not None:
        return chance / 100.0
    return 1.0 if player.get("status") == "a" else 0.0


# Priors: what a player's scoring rate is shrunk toward before this season has
# said much about him. Shared so that every model reads the same prior, for the
# same reason the window above is shared.

# Rough points-per-90 for a regular starter, by position. The fallback for a
# player with no usable last season: promoted clubs, new signings, and anyone
# who barely played. Same values as baseline-v1's, which keeps its own copy
# because it is frozen.
POSITION_PRIOR_PP90 = {"GKP": 3.2, "DEF": 3.3, "MID": 3.6, "FWD": 3.8}

# Pseudo-minutes the positional prior is worth. Constant, as in baseline-v1: it
# knows nothing about the player, so it only has to stop cameos topping the
# table.
POSITION_PRIOR_MINUTES = 270.0

# Last season's rate is only a prior if it rests on a regular's season: 30 full
# matches. Tuned on the backtest over GW2-5 (#30); lower floors let squad
# players' inflated rates in and did worse than the positional prior on MAE,
# 900 by 0.025.
PLAYER_PRIOR_FLOOR_MINUTES = 2700

# Pseudo-minutes last season's rate is worth at the start of this one, and the
# season minutes by which that has halved (19 full matches — midseason). 900 is
# where the backtest's gain flattened out; heavier weights moved MAE by at most
# 0.002. The decay has to run on the whole season rather than the lookback
# window: the window never holds more than LOOKBACK rounds, so against it a
# fixed prior would weigh as much in GW30 as in GW3.
PLAYER_PRIOR_MINUTES = 900.0
PLAYER_PRIOR_HALF_LIFE_MINUTES = 1710.0


def previous_season(events):
    """Last season's name as history_past spells it, e.g. "2025/26".

    Read from GW1's deadline, which falls in the season's first calendar year.
    Named rather than taken as history_past's last row: a player absent from
    the league last season has an older season last, and a prior from two or
    more years ago describes a different player.
    """
    first = next((e for e in events if e["id"] == 1), None)
    if first is None or not first.get("deadline_time"):
        raise SystemExit("No GW1 deadline in this snapshot to date the season from.")
    year = int(first["deadline_time"][:4])
    return f"{year - 1}/{str(year)[2:]}"


# FPL points for a goal by position, and for an assist. Used to price last
# season's expected goals and assists in the same units as its points.
GOAL_POINTS = {"GKP": 6, "DEF": 6, "MID": 5, "FWD": 4}
ASSIST_POINTS = 3

# How much of the prior comes from last season's expected rather than realised
# goals and assists: 0 is points as scored, 1 is fully expected. On the
# backtest over GW2-5 (#30), xG helped at every floor below 2700 and tied at
# 2700, where a regular's goals have had a season to settle near his xG.
# Half, rather than none: it is never worse there, and it is the safer bet
# for a player whose last season ran hot or cold in front of goal.
XG_WEIGHT = 0.5


def expected_points(row, position):
    """Last season's points with realised goals and assists priced at xG and xA.

    Everything else — appearances, clean sheets, bonus, cards — stays as
    scored. None if the row or the position lacks what the swap needs, so the
    caller falls back to realised points rather than to a guess.
    """
    goal_points = GOAL_POINTS.get(position)
    try:
        xg = float(row["expected_goals"])
        xa = float(row["expected_assists"])
        goals, assists = row["goals_scored"], row["assists"]
    except (KeyError, TypeError, ValueError):
        return None
    if goal_points is None:
        return None
    realised = goals * goal_points + assists * ASSIST_POINTS
    expected = xg * goal_points + xa * ASSIST_POINTS
    return (row.get("total_points") or 0) - realised + expected


def player_prior(history_past, season, position=None, xg_weight=None):
    """Points per 90 in `season`, or None if he did not play enough of it.

    Blends realised points with expected_points by `xg_weight`. Goals and
    assists are last season's noisiest points, and xG and xA are steadier
    estimates of the same thing. Without a position or xG fields, realised
    points alone. `xg_weight` defaults to XG_WEIGHT, read at call time.
    """
    row = next((r for r in history_past if r.get("season_name") == season), None)
    if row is None:
        return None
    minutes = row.get("minutes") or 0
    if minutes < PLAYER_PRIOR_FLOOR_MINUTES:
        return None
    if xg_weight is None:
        xg_weight = XG_WEIGHT
    points = row.get("total_points") or 0
    expected = expected_points(row, position) if xg_weight else None
    if expected is not None:
        points = (1 - xg_weight) * points + xg_weight * expected
    return points * 90.0 / minutes


def season_minutes(history, target_gw, usable):
    """Minutes this season in settled rounds before the target, not windowed."""
    return sum(h.get("minutes", 0) or 0 for h in history
               if h.get("round") is not None and h["round"] < target_gw
               and h["round"] in usable)


def prior(position, last_season_pp90, minutes_this_season,
          player_prior_minutes=PLAYER_PRIOR_MINUTES):
    """(points per 90, pseudo-minutes) to shrink a player's rate toward.

    Last season's rate where there is one, fading as this season accumulates;
    otherwise the positional prior at its constant weight.
    """
    if last_season_pp90 is None:
        return POSITION_PRIOR_PP90.get(position, 3.5), POSITION_PRIOR_MINUTES
    fade = PLAYER_PRIOR_HALF_LIFE_MINUTES / (
        PLAYER_PRIOR_HALF_LIFE_MINUTES + minutes_this_season)
    return last_season_pp90, player_prior_minutes * fade


def shrunk_pp90(obs_points, obs_minutes, prior_pp90, prior_minutes):
    """Observed points per 90, blended with `prior_minutes` of the prior."""
    prior_points = prior_pp90 * (prior_minutes / 90.0)
    return (obs_points + prior_points) / ((obs_minutes + prior_minutes) / 90.0)


def expected_minutes(recent, player):
    """Minutes expected per fixture: recent average, capped, times availability.

    Per fixture, not per round. A past double gameweek puts up to 180 minutes
    in one round; averaging that raw and only then clipping to 90 drags the
    mean upward across the whole lookback window. The same logic as
    baseline-v1's, which keeps its own copy because it is frozen.
    """
    avg_minutes, _ = weighted([r["minutes"] / r["fixtures"] for r in recent])
    return min(avg_minutes, 90.0) * availability(player)
