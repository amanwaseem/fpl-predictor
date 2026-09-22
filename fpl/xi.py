"""The best legal starting XI from a pool of players.

Legal means a valid formation — 1 GKP, 3-5 DEF, 2-5 MID, 1-3 FWD, eleven in
all — and at most three players from any one club. There is no budget, which
is what makes this exactly solvable without an integer programming solver
(SPEC section 6). The squad optimiser in section 7 adds the budget and reuses
this selector; the dependency does not run the other way.

The scoring harness calls this twice per gameweek: once choosing by predicted
points ("captured" — what the model's XI actually scored) and once by actual
points ("optimum" — the best any XI could have scored).

## Why a flow, not a greedy pick

Greedy — take the best remaining player that still fits — is wrong once the
club cap binds. Taking a fourth-best defender from a crowded club can block a
better midfielder later, and greedy has no way to undo the choice. The scratch
GW4 review used greedy and said so; the harness needs the exact answer.

For a fixed formation the problem is a bipartite matching with capacities:
each player is an edge from their position to their club, each position must
take exactly its quota, and each club at most three. That is a minimum-cost
flow, and successive shortest paths solves it exactly. Doing that for each of
the eight formations and keeping the best is exact overall.

Costs are integers — points in hundredths, since entries carry two decimal
places and actuals are whole — so no float comparison decides which XI wins.
Ties are broken toward lower player ids, making the chosen XI deterministic and
not merely its total.
"""

FORMATIONS = [
    (defenders, midfielders, 10 - defenders - midfielders)
    for defenders in range(3, 6)
    for midfielders in range(2, 6)
    if 1 <= 10 - defenders - midfielders <= 3
]
POSITIONS = ("GKP", "DEF", "MID", "FWD")
MAX_PER_CLUB = 3
XI_SIZE = 11

# Values are compared in hundredths of a point.
SCALE = 100


class _Flow:
    """Minimal min-cost flow: successive shortest paths with Bellman-Ford.

    Bellman-Ford rather than Dijkstra because player edges carry negative
    costs. The graph is small — a few dozen nodes, one edge per player — and at
    most eleven augmentations are needed, so there is nothing to optimise.
    """

    def __init__(self, n_nodes):
        self.adj = [[] for _ in range(n_nodes)]
        self.to, self.cap, self.cost = [], [], []

    def add(self, u, v, cap, cost):
        """Add an edge and its residual twin; return the forward edge's index."""
        for a, b, c, w in ((u, v, cap, cost), (v, u, 0, -cost)):
            self.adj[a].append(len(self.to))
            self.to.append(b)
            self.cap.append(c)
            self.cost.append(w)
        return len(self.to) - 2

    def run(self, source, sink, wanted):
        """Push up to `wanted` units at minimum cost. Returns (flow, cost)."""
        n = len(self.adj)
        flow = total = 0
        while flow < wanted:
            dist = [None] * n
            via = [None] * n
            dist[source] = 0
            changed = True
            while changed:
                changed = False
                for u in range(n):
                    if dist[u] is None:
                        continue
                    for e in self.adj[u]:
                        v = self.to[e]
                        if self.cap[e] > 0 and (dist[v] is None
                                                or dist[u] + self.cost[e] < dist[v]):
                            dist[v] = dist[u] + self.cost[e]
                            via[v] = e
                            changed = True
            if dist[sink] is None:
                break
            # Every edge here has unit or small capacity; push one unit at a
            # time, which keeps the path bookkeeping trivial.
            v = sink
            while v != source:
                e = via[v]
                self.cap[e] -= 1
                self.cap[e ^ 1] += 1
                v = self.to[e ^ 1]
            flow += 1
            total += dist[sink]
        return flow, total


def _best_for_formation(pool, scaled, quotas, clubs):
    """Exact best XI for one formation, or None if the pool cannot fill it."""
    source, sink = 0, 1
    pos_node = {pos: 2 + i for i, pos in enumerate(POSITIONS)}
    club_node = {club: 2 + len(POSITIONS) + i for i, club in enumerate(clubs)}
    graph = _Flow(2 + len(POSITIONS) + len(clubs))

    for pos in POSITIONS:
        graph.add(source, pos_node[pos], quotas[pos], 0)
    for club in clubs:
        graph.add(club_node[club], sink, MAX_PER_CLUB, 0)

    # Tie-break: among equal totals, prefer lower player ids. The weight is
    # large enough that no combination of tie-breaks can outweigh a single
    # hundredth of a point.
    weight = XI_SIZE * len(pool) + 1
    edges = []
    for rank, player in enumerate(pool):
        cost = -scaled[rank] * weight + rank
        edge = graph.add(pos_node[player["position"]], club_node[player["team"]], 1, cost)
        edges.append((edge, player))

    flow, cost = graph.run(source, sink, XI_SIZE)
    if flow < XI_SIZE:
        return None
    chosen = [player for edge, player in edges if graph.cap[edge] == 0]
    return cost, chosen


def best_xi(players, value):
    """The legal XI maximising `value`, as (total, players, formation).

    `players` are dicts carrying at least "player_id", "position" and "team".
    `value` maps a player to the number being maximised — predicted points for
    "captured", actual points for "optimum". `total` is the sum of `value` over
    the XI; `formation` is a string such as "4-4-2".

    Raises ValueError if no legal XI exists: too few players in a position, or
    too few clubs to stay within three per club. A pool that cannot field a
    legal XI is refused rather than filled with whatever is available, because
    an illegal XI's total would be compared against legal ones.
    """
    unknown = sorted({p["position"] for p in players} - set(POSITIONS))
    if unknown:
        raise ValueError(f"unknown position(s) in pool: {unknown}")

    pool = sorted(players, key=lambda p: p["player_id"])
    scaled = [round(value(p) * SCALE) for p in pool]
    clubs = sorted({p["team"] for p in pool})

    best = None
    for defenders, midfielders, forwards in FORMATIONS:
        quotas = {"GKP": 1, "DEF": defenders, "MID": midfielders, "FWD": forwards}
        result = _best_for_formation(pool, scaled, quotas, clubs)
        if result is None:
            continue
        cost, chosen = result
        if best is None or cost < best[0]:
            best = (cost, chosen, f"{defenders}-{midfielders}-{forwards}")

    if best is None:
        counts = {pos: sum(p["position"] == pos for p in pool) for pos in POSITIONS}
        raise ValueError(
            f"no legal XI in a pool of {len(pool)} players across {len(clubs)} "
            f"club(s) — by position {counts}. A legal XI needs 1 GKP, 3 DEF, "
            "2 MID, 1 FWD at minimum and at most three per club."
        )

    _, chosen, formation = best
    order = {pos: i for i, pos in enumerate(POSITIONS)}
    chosen.sort(key=lambda p: (order[p["position"]], -value(p), p["player_id"]))
    return sum(value(p) for p in chosen), chosen, formation
