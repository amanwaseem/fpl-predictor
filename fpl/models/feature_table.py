"""One row of model inputs per player per gameweek, from before its deadline (#84).

The learned model (#85) trains on these rows across ten past seasons and the
current one, and predicts from them. fpl.db.features reads a season from the
database into a SeasonData, calls build_gameweek for each gameweek, and
writes the rows to `player_gameweek_features`. This module is pure: it reads
nothing and imports no database, so the prediction runtime can use it too,
and the leak test (tests/test_feature_table.py) can poison its input freely.

## What a row may know

Rule 2: a gameweek's features come only from what was known before its
deadline. Here that means:

- **Results** only from rounds before the gameweek that had settled, as the
  logged models read them (fpl.models.features.recent_history): every row,
  every score and every rating below is built from those rounds alone.
- **The fixture list and each player's club** for the gameweek. Both are
  published before the deadline; neither is a result. One caveat: the
  database keeps each fixture's final gameweek, not the one first published,
  so a match postponed after a deadline counts in the gameweek it was played.
  The archive keeps no earlier schedule to build from.
- **Price** as the last value seen before the gameweek, so none for GW1.
- **Availability** only where a committed entry recorded it (the live season),
  since entries are made before deadlines. Past seasons have none.

Everything else about the gameweek itself (its rows, its scores, a later
snapshot's view of a player) is never read. The leak test corrupts all of it
and requires the rows to come out the same.

## Columns

Windows are a player's most recent rounds with a row before the gameweek,
1, 3 or 5 of them, or the season to date; per-90 rates are over the window's
minutes, and missing when it has none. Expected goals and assists are missing
for seasons before FPL published them (2022/23).
"""

from collections import defaultdict
from dataclasses import dataclass, field

from fpl.models.features import settled_before
from fpl.models.teams import Ratings, clean_sheet_probability, team_matches

FEATURE_VERSION = "features-v1"
WINDOWS = (3, 5)


@dataclass
class SeasonData:
    """A season, shaped as the snapshot loader and the models read it.

    fixtures: the fixtures endpoint's dicts (id, event, team_h, team_a,
    team_h_score, team_a_score, finished). histories: element-summary history
    rows per element (round, fixture, was_home, minutes, total_points,
    expected_goals, expected_assists, bonus, value). settled: gameweeks whose
    results are final. clubs: per element, [(team, first gameweek, last
    gameweek)]. players: per element, position and FPL's code. previous: the
    season before, per code, {"minutes", "points"}. status: per (gameweek,
    element), as a committed entry recorded it.
    """

    season: str
    fixtures: list[dict]
    histories: dict[int, list[dict]]
    settled: set[int]
    clubs: dict[int, list[tuple[int, int, int]]]
    players: dict[int, dict]
    previous: dict[int, dict] = field(default_factory=dict)
    status: dict[tuple[int, int], str] = field(default_factory=dict)


def season_before(season):
    """'2025/26' for '2026/27'."""
    start = int(season[:4]) - 1
    return f"{start}/{(start + 1) % 100:02d}"


def season_after(season):
    """'2027/28' for '2026/27'."""
    start = int(season[:4]) + 1
    return f"{start}/{(start + 1) % 100:02d}"


def club_at(spells, gameweek):
    for team, first, last in spells:
        if first <= gameweek <= last:
            return team
    return None


def ratings_before(data, gameweek, usable):
    """Team ratings from the settled rounds before `gameweek`, or None before any.

    Rated on expected goals where the season has them, goals where it does not.
    """
    matches = team_matches(data.fixtures, data.histories, gameweek, usable)
    source = "xg" if any(m["xg_for"] for m in matches) else "goals"
    # A league with nothing scored yet (or no rounds) has no rate to rate
    # clubs against; Ratings would stop the run, so there are no ratings.
    if sum(m[f"{source}_for"] for m in matches) <= 0:
        return None
    return Ratings(matches, source=source)


def _rounds(rows):
    """Per round, most recent first: minutes, points, xG, xA, bonus, fixtures, a 60."""
    by_round = defaultdict(
        lambda: {
            "minutes": 0,
            "points": 0,
            "xg": None,
            "xa": None,
            "bonus": 0,
            "sixty": False,
            "value": None,
        }
    )
    for row in sorted(rows, key=lambda r: r["round"]):
        entry = by_round[row["round"]]
        minutes = row.get("minutes") or 0
        entry["minutes"] += minutes
        entry["points"] += row.get("total_points") or 0
        entry["bonus"] += row.get("bonus") or 0
        entry["sixty"] = entry["sixty"] or minutes >= 60
        for key, source in (("xg", "expected_goals"), ("xa", "expected_assists")):
            if row.get(source) is not None:
                entry[key] = (entry[key] or 0.0) + float(row[source])
        if row.get("value") is not None:
            entry["value"] = row["value"]
    return [by_round[r] for r in sorted(by_round, reverse=True)]


def _mean(values):
    return sum(values) / len(values) if values else None


def _per90(rounds, key):
    minutes = sum(r["minutes"] for r in rounds)
    values = [r[key] for r in rounds if r[key] is not None]
    if minutes <= 0 or not values:
        return None
    return sum(values) / minutes * 90


def player_features(rounds):
    """The form columns, from a player's rounds before the gameweek, most recent first."""
    row = {
        "minutes_last1": rounds[0]["minutes"] if rounds else None,
        "appearances_last5": sum(r["minutes"] > 0 for r in rounds[:5]),
        "sixty_last5": sum(r["sixty"] for r in rounds[:5]),
        "season_rounds": len(rounds),
        "season_minutes": sum(r["minutes"] for r in rounds),
        "season_points": sum(r["points"] for r in rounds),
        "points_per90_season": _per90(rounds, "points"),
        "xgi_per90_season": _xgi(rounds),
        "bonus_per90_season": _per90(rounds, "bonus"),
    }
    for k in WINDOWS:
        window = rounds[:k]
        row[f"minutes_mean{k}"] = _mean([r["minutes"] for r in window])
        row[f"points_per90_{k}"] = _per90(window, "points")
    row["xg_per90_5"] = _per90(rounds[:5], "xg")
    row["xa_per90_5"] = _per90(rounds[:5], "xa")
    return row


def _xgi(rounds):
    xg, xa = _per90(rounds, "xg"), _per90(rounds, "xa")
    return None if xg is None and xa is None else (xg or 0.0) + (xa or 0.0)


def build_gameweek(data, gameweek):
    """Every player whose club has a fixture in `gameweek`, one dict each."""
    usable = {g for g in data.settled if g < gameweek}
    ratings = ratings_before(data, gameweek, usable)
    fixtures = [f for f in data.fixtures if f.get("event") == gameweek]
    by_team = defaultdict(list)
    for f in fixtures:
        by_team[f["team_h"]].append((f, True))
        by_team[f["team_a"]].append((f, False))

    rows = []
    for element, player in sorted(data.players.items()):
        team = club_at(data.clubs.get(element, []), gameweek)
        if team is None or team not in by_team:
            continue
        # The logged models' boundary: fpl.models.features.settled_before.
        before = [h for h in data.histories.get(element, []) if settled_before(h, gameweek, usable)]
        rounds = _rounds(before)
        previous = data.previous.get(player["code"])
        prices = [r["value"] for r in rounds if r["value"] is not None]
        row = {
            "season": data.season,
            "gameweek": gameweek,
            "element_id": element,
            "player_code": player["code"],
            "position": player["position"],
            "team_id": team,
            "n_fixtures": len(by_team[team]),
            "n_home": sum(home for _, home in by_team[team]),
            "price": prices[0] if prices else None,
            "status": data.status.get((gameweek, element)),
            "last_season_minutes": previous["minutes"] if previous else None,
            "last_season_points_per90": (
                previous["points"] / previous["minutes"] * 90
                if previous and previous["minutes"] > 0
                else None
            ),
            "feature_version": FEATURE_VERSION,
        }
        row |= player_features(rounds)
        row |= _team_features(ratings, team, by_team[team])
        rows.append(row)
    return rows


def _team_features(ratings, team, fixtures):
    if ratings is None:
        return dict.fromkeys(
            (
                "team_attack",
                "team_defence",
                "opponent_attack",
                "opponent_defence",
                "expected_goals_for",
                "expected_goals_against",
                "expected_clean_sheets",
            )
        )
    opponents = [f["team_a"] if home else f["team_h"] for f, home in fixtures]
    goals = [ratings.fixture_goals(f, team) for f, _ in fixtures]
    return {
        "team_attack": ratings.attack.get(team, 1.0),
        "team_defence": ratings.defence.get(team, 1.0),
        "opponent_attack": _mean([ratings.attack.get(o, 1.0) for o in opponents]),
        "opponent_defence": _mean([ratings.defence.get(o, 1.0) for o in opponents]),
        "expected_goals_for": sum(g for g, _ in goals),
        "expected_goals_against": sum(a for _, a in goals),
        "expected_clean_sheets": sum(clean_sheet_probability(a) for _, a in goals),
    }
