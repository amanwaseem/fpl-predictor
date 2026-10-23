"""Turning snapshot rows into model inputs.

Shared by every model that reasons from recent form. The window and the decay
are here rather than in any one model because two models disagreeing about
what "recent form" means would make their predictions incomparable, and the
log's whole value is that entries can be compared.
"""

import math

from fpl.ingest.snapshot import availability

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
# season minutes by which that has halved (19 full matches — midseason). 1800
# was chosen held out: tuned on three of GW2-5 and scored on the fourth, every
# fold picked it, alone and alongside fpl.models.teams. The decay has to run on the
# whole season rather than the lookback window: the window never holds more
# than LOOKBACK rounds, so against it a fixed prior would weigh as much in GW30
# as in GW3.
PLAYER_PRIOR_MINUTES = 1800.0
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

# Points for a clean sheet, by position, for 60+ minutes.
CLEAN_SHEET_POINTS = {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}

# Defensive contribution: DEFENSIVE_CONTRIBUTION_POINTS for reaching the
# position's threshold of clearances, blocks, interceptions and tackles (plus
# recoveries, outside defence) in one match. History rows carry the count, not
# the points. Goalkeepers have no threshold and never earn them.
DEFENSIVE_CONTRIBUTION_THRESHOLD = {"DEF": 10, "MID": 12, "FWD": 12}
DEFENSIVE_CONTRIBUTION_POINTS = 2

# Which history fields make up the count, by position: recoveries count
# outside defence only. On 20260922 these reproduce `defensive_contribution`
# on every outfield row this season.
DEFENSIVE_CONTRIBUTION_FIELDS = {
    "DEF": ("clearances_blocks_interceptions", "tackles"),
    "MID": ("clearances_blocks_interceptions", "tackles", "recoveries"),
    "FWD": ("clearances_blocks_interceptions", "tackles", "recoveries"),
}


def defensive_count(row, position):
    """The defensive-contribution count in a row, as `position` counts it.

    A history_past row is counted as he was classed last season, so a player
    who has moved between DEF and MID carries a count with or without
    recoveries that his threshold now does not use. Rebuilt from its fields
    where the row has them; the row's own count where it does not.
    """
    fields = DEFENSIVE_CONTRIBUTION_FIELDS.get(position)
    if fields and all(f in row for f in fields):
        return sum(row[f] or 0 for f in fields)
    return row.get("defensive_contribution") or 0


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


def player_prior(history_past, season, position=None, xg_weight=None, floor=None):
    """Points per 90 in `season`, or None if he did not play enough of it.

    Blends realised points with expected_points by `xg_weight`. Goals and
    assists are last season's noisiest points, and xG and xA are steadier
    estimates of the same thing. Without a position or xG fields, realised
    points alone. `xg_weight` and `floor` default to XG_WEIGHT and
    PLAYER_PRIOR_FLOOR_MINUTES, read at call time.
    """
    parts = player_prior_parts(history_past, season, position, xg_weight, floor)
    return None if parts is None else parts["total"]


def player_prior_parts(history_past, season, position=None, xg_weight=None, floor=None):
    """player_prior split the way a fixture-aware model splits this season.

    {"total", "attack", "clean_sheet"}, each points per 90: attacking returns
    (goals and assists, blended with xG and xA by `xg_weight`), clean-sheet
    points, and everything. Plus "defensive_count", the defensive-contribution
    count per 90 as his position now counts it: a count, not points, because
    a season row cannot say in how many matches it reached the threshold
    (#45, #48). And "conceded" and "saves", from season_goalkeeping_parts. The
    parts come from the same season row as the
    total, so a model that shrinks each part of this season toward the
    matching part here, at one weight, gets parts that add back up to its
    total. None when player_prior would be.
    """
    row = next((r for r in history_past if r.get("season_name") == season), None)
    if row is None:
        return None
    minutes = row.get("minutes") or 0
    if minutes < (PLAYER_PRIOR_FLOOR_MINUTES if floor is None else floor):
        return None
    if xg_weight is None:
        xg_weight = XG_WEIGHT

    realised = ((row.get("goals_scored") or 0) * GOAL_POINTS.get(position, 0)
                + (row.get("assists") or 0) * ASSIST_POINTS)
    attack = realised
    expected = expected_points(row, position) if xg_weight else None
    if expected is not None:
        # expected_points is the total with attack swapped for xG and xA, so
        # its attack is what it adds back over the rest of the season.
        points = row.get("total_points") or 0
        attack = (1 - xg_weight) * realised + xg_weight * (expected - (points - realised))
    total = (row.get("total_points") or 0) - realised + attack
    clean_sheet = (row.get("clean_sheets") or 0) * CLEAN_SHEET_POINTS.get(position, 0)
    per_90 = 90.0 / minutes
    return {"total": total * per_90, "attack": attack * per_90,
            "clean_sheet": clean_sheet * per_90,
            "defensive_count": defensive_count(row, position) * per_90,
            **dict(zip(("conceded", "saves"), season_goalkeeping_parts(row, position),
                       strict=True))}


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


# Splitting a player's rate into the parts a fixture moves. Attacking returns
# scale with the side's expected goals; clean-sheet points are re-priced from
# the chance of keeping one; the rest is left alone. Shared because a logged
# model and the comparators it is judged against have to split alike.

def attack_points(row, position):
    """Points from goals and assists in one history row."""
    return ((row.get("goals_scored") or 0) * GOAL_POINTS.get(position, 0)
            + (row.get("assists") or 0) * ASSIST_POINTS)


def clean_sheet_points(row, position):
    """Points from a clean sheet in one history row."""
    return (row.get("clean_sheets") or 0) * CLEAN_SHEET_POINTS.get(position, 0)


def defensive_points(row, position):
    """Points from defensive contribution in one history row."""
    threshold = DEFENSIVE_CONTRIBUTION_THRESHOLD.get(position)
    if threshold is None or (row.get("defensive_contribution") or 0) < threshold:
        return 0
    return DEFENSIVE_CONTRIBUTION_POINTS


def chance_of_defensive_contribution(count_per_90, position):
    """P(reaching the position's threshold in a full match), from his rate per 90.

    The count in a match is read as Poisson around his rate. On 20260922's
    full matches a player's counts vary about as much as Poisson says
    (variance over mean 1.02 for DEF, 0.81 for MID), and the chances it gives
    summed to 46.8 DEF hits against 45, and 20.7 MID against 18. 0 for a
    position with no threshold.
    """
    threshold = DEFENSIVE_CONTRIBUTION_THRESHOLD.get(position)
    if threshold is None or count_per_90 <= 0:
        return 0.0
    term = below = math.exp(-count_per_90)
    for k in range(1, threshold):
        term *= count_per_90 / k
        below += term
    return max(0.0, 1.0 - below)


def settled_before(row, target_gw, usable):
    """Whether a history row is from a settled round before the target (rule 2)."""
    return row.get("round") is not None and row["round"] < target_gw and row["round"] in usable


def league_rates(elements, positions, histories, target_gw, usable, parts):
    """League per-90 rates by position, one per function in `parts`.

    Each of `parts` maps (history row, position) to that row's amount. Summed
    over every player's settled rows before the target and divided by their
    minutes, so a position's prior for each part comes from what it actually
    earned, from the same rounds a player is judged on.
    """
    totals = {}
    for player in elements:
        position = positions.get(player["element_type"], "UNK")
        t = totals.setdefault(position, [0.0] * len(parts) + [0])
        for row in histories.get(player["id"], []):
            if not settled_before(row, target_gw, usable):
                continue
            for i, part in enumerate(parts):
                t[i] += part(row, position)
            t[-1] += row.get("minutes") or 0
    return {pos: tuple(x * 90.0 / t[-1] if t[-1] else 0.0 for x in t[:-1])
            for pos, t in totals.items()}


def positional_rates(elements, positions, histories, target_gw, usable):
    """League points-per-90 from attacking returns and clean sheets, by position.

    The positional prior split the same way a player is split, from the same
    settled rounds, so a player with few minutes and no usable last season is
    shrunk toward what his position actually earns from each part.
    """
    return league_rates(elements, positions, histories, target_gw, usable,
                        (attack_points, clean_sheet_points))


def defensive_contribution_points(count_per_90, position, window, expected):
    """Expected DC points in one fixture: his count per 90, his minutes expected.

    The chance of reaching the threshold is steeply convex in minutes, so it
    is taken at his usual minutes when he plays, times the chance he plays:
    a defender who plays 90 or nothing keeps his full-match chance, scaled
    down; one who always plays 60 is judged on a 60-minute count.
    """
    played = [r["minutes"] for r in window if (r.get("minutes") or 0) > 0]
    if not played or expected <= 0:
        return 0.0
    usual = sum(played) / len(played)
    return (DEFENSIVE_CONTRIBUTION_POINTS * min(1.0, expected / usual)
            * chance_of_defensive_contribution(count_per_90 * usual / 90.0, position))


def positional_defensive_rates(elements, positions, histories, target_gw, usable):
    """League defensive contribution per 90, by position: (count, points)."""
    return league_rates(elements, positions, histories, target_gw, usable,
                        (lambda row, _: row.get("defensive_contribution") or 0,
                         defensive_points))


def split_rates(observed, minutes, priors, prior_minutes):
    """(total, attack, clean sheet, rest) points per 90, each shrunk alike.

    `observed` and `priors` are (total, attack, clean sheet) as points and as
    points per 90. Every part is shrunk toward its own prior at the same
    weight, so the parts add back to the total and the rest is itself a
    shrunk rate — last season's or the position's rest, blended with this
    season's — rather than whatever the other parts leave over.
    """
    total, attack, clean_sheet = (shrunk_pp90(o, minutes, p, prior_minutes)
                                  for o, p in zip(observed, priors, strict=True))
    return total, attack, clean_sheet, total - attack - clean_sheet


def chance_of_sixty(rows, player):
    """Decayed share of recent fixtures he played 60+ minutes in, times availability.

    `rows` are the window's raw history rows (recent_rows). Per fixture, so a
    double counts both of its matches.
    """
    by_round = {}
    for row in rows:
        played, total = by_round.get(row["round"], (0, 0))
        by_round[row["round"]] = (played + ((row.get("minutes") or 0) >= 60), total + 1)
    total = weight_sum = 0.0
    for i, rnd in enumerate(sorted(by_round, reverse=True)):
        played, fixtures = by_round[rnd]
        total += DECAY ** i * played / fixtures
        weight_sum += DECAY ** i
    return (total / weight_sum if weight_sum else 0.0) * availability(player)


# Goals conceded and saves: the goalkeeping parts of the rest (#48). Rebuilt
# from their fields, these rules reproduce total_points on every played row of
# 20261010T041511Z: -1 per 2 conceded for GKP and DEF at any minutes, and 1 per
# 3 saves for GKP. Both floor per match, so their expectation is taken over
# the count's distribution, not read off its mean.
CONCEDED_POINTS = {"GKP": -1, "DEF": -1}
CONCEDED_PER_POINT = 2
SAVE_POINTS = {"GKP": 1}
SAVES_PER_POINT = 3


def poisson_floor_mean(mean, per):
    """E[floor(X / per)] for X ~ Poisson(mean).

    The sum over j >= 1 of P(X >= j * per), from the pmf, out to where the
    tail no longer moves the result.
    """
    if mean <= 0:
        return 0.0
    limit = int(mean + 12 * mean ** 0.5 + 20)
    pmf = math.exp(-mean)
    tail = 1.0  # P(X >= k), starting at k = 0
    total = 0.0
    for k in range(limit + 1):
        if k and k % per == 0:
            total += tail
        tail -= pmf
        pmf *= mean / (k + 1)
    return total


def conceded_points(row, position):
    """Points lost to goals conceded in one history row."""
    return CONCEDED_POINTS.get(position, 0) * ((row.get("goals_conceded") or 0)
                                                // CONCEDED_PER_POINT)


def save_points(row, position):
    """Points from saves in one history row."""
    return SAVE_POINTS.get(position, 0) * ((row.get("saves") or 0) // SAVES_PER_POINT)


def season_goalkeeping_parts(row, position):
    """(conceded, saves) points per 90 from a season-total history_past row.

    A season row holds totals, not matches, and both parts floor per match.
    So each is priced as a match at his per-90 average, through the same
    Poisson floor the prediction uses, rather than as total / 2 or / 3.
    """
    minutes = row.get("minutes") or 0
    if not minutes:
        return 0.0, 0.0
    per_90 = 90.0 / minutes
    conceded = CONCEDED_POINTS.get(position, 0) * poisson_floor_mean(
        (row.get("goals_conceded") or 0) * per_90, CONCEDED_PER_POINT)
    saves = SAVE_POINTS.get(position, 0) * poisson_floor_mean(
        (row.get("saves") or 0) * per_90, SAVES_PER_POINT)
    return conceded, saves


def league_save_rate(elements, positions, histories, target_gw, usable):
    """Saves per expected goal conceded, over every keeper's settled rows.

    One rate for every keeper. His own season, shrunk toward this, was tried
    held out over GW2-5 (#48): every fold chose to shrink it all the way. Over
    2700+ minutes keepers' 2025/26 rates ran only 1.78 to 2.21, so a few
    matches of his own say little the league's does not.
    """
    saves = xgc = 0.0
    for player in elements:
        if positions.get(player["element_type"]) != "GKP":
            continue
        for row in histories.get(player["id"], []):
            if not settled_before(row, target_gw, usable):
                continue
            saves += row.get("saves") or 0
            xgc += float(row.get("expected_goals_conceded") or 0)
    return saves / xgc if xgc else 0.0


def positional_goalkeeping_rates(elements, positions, histories, target_gw, usable):
    """League (conceded, saves) points per 90 by position."""
    return league_rates(elements, positions, histories, target_gw, usable,
                        (conceded_points, save_points))


def split_parts(observed, minutes, priors, prior_minutes):
    """split_rates for any number of parts: (total, [parts], rest), per 90.

    `observed` and `priors` are (total, part, part, ...). Each is shrunk
    toward its own prior at one weight, so the parts and the rest add back up
    to the total.
    """
    total, *parts = (shrunk_pp90(o, minutes, p, prior_minutes)
                     for o, p in zip(observed, priors, strict=True))
    return total, parts, total - sum(parts)
