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
