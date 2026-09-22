"""Accuracy metrics for predicted versus actual points.

Pure functions over (predicted, actual) pairs, with no knowledge of files,
snapshots or gameweeks. The scoring harness applies them to committed entries
and the backtest (#29) applies them to replayed ones; both have to compute
"how wrong" identically, or a backtest result could not be compared with the
track record it is meant to anticipate.
"""

import math
from itertools import chain


def _check(pairs):
    if not pairs:
        raise ValueError("no (predicted, actual) pairs to score")


def mae(pairs):
    """Mean absolute error."""
    _check(pairs)
    return sum(abs(p - a) for p, a in pairs) / len(pairs)


def rmse(pairs):
    """Root mean squared error. Punishes a missed haul harder than MAE does."""
    _check(pairs)
    return math.sqrt(sum((p - a) ** 2 for p, a in pairs) / len(pairs))


def bias(pairs):
    """Mean of predicted minus actual. Positive means the model overpredicts."""
    _check(pairs)
    return sum(p - a for p, a in pairs) / len(pairs)


def average_ranks(values):
    """1-based ranks, with tied values sharing the mean of the ranks they span.

    Hundreds of players tie at 0.0 predicted. Ranking them in input order
    instead would make the correlation depend on the order the entry happens to
    list them in, which is not a property of the model.
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        shared = (start + end) / 2 + 1
        for k in range(start, end + 1):
            ranks[order[k]] = shared
        start = end + 1
    return ranks


def spearman(predicted, actual):
    """Spearman rank correlation with average-rank tie handling.

    Returns None when it is undefined: fewer than two values, or either side
    constant. A constant predictor — the all-zero comparator is one — has no
    ordering to correlate. Reporting 0 would claim it ranks players no better
    than chance, which is a statement about an ordering it does not have, and
    raising would stop a scoring run over one comparator.
    """
    if len(predicted) != len(actual):
        raise ValueError("predicted and actual differ in length")
    if len(predicted) < 2:
        return None
    rp, ra = average_ranks(predicted), average_ranks(actual)
    mp, ma = sum(rp) / len(rp), sum(ra) / len(ra)
    cov = sum((x - mp) * (y - ma) for x, y in zip(rp, ra))
    spread = math.sqrt(sum((x - mp) ** 2 for x in rp) * sum((y - ma) ** 2 for y in ra))
    if spread == 0:
        return None
    return cov / spread


def summary(pairs):
    """The standard block of metrics for one set of pairs."""
    _check(pairs)
    return {
        "n": len(pairs),
        "mae": mae(pairs),
        "rmse": rmse(pairs),
        "bias": bias(pairs),
        "spearman": spearman([p for p, _ in pairs], [a for _, a in pairs]),
    }


def pooled_summary(groups):
    """Metrics over every pair in every group, as one population.

    Cumulative figures are pooled across player-gameweek rows, not averaged
    across gameweeks (SPEC section 6). With unequal row counts per gameweek the
    two differ, and only the pooled figure answers "how wrong has this model
    been so far".
    """
    return summary(list(chain.from_iterable(groups)))
