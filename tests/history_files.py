"""A tiny archive in vaastav's layout, for the backfill's tests (#79).

Two clubs, three players and a manager, two fixtures: GW1 Team 1 v Team 2,
GW2 Team 2 v Team 1. Player 3 moves from Team 1 to Team 2 after GW1.
"""

import csv
from pathlib import Path

from fpl.db.models import PlayerFixtureStats

STAT_NAMES = [
    c.name
    for c in PlayerFixtureStats.__table__.columns
    if c.name not in ("season", "element_id", "fixture_id", "snapshot_id")
]


def player(pid, team, element_type=3, code=None, web_name=None):
    return {
        "id": pid,
        "code": code or 1000 + pid,
        "first_name": f"First{pid}",
        "second_name": f"Second{pid}",
        "web_name": web_name or f"p{pid}",
        "element_type": element_type,
        "team": team,
        "team_code": {1: 3, 2: 88}[team],
    }


def gw_row(element, fixture, gw, *, home, opponent, kickoff, scores=(2, 1), points=5):
    row = {name: 0 for name in STAT_NAMES}
    row.update(
        {
            "name": f"p{element}",
            "element": element,
            "fixture": fixture,
            "round": gw,
            "GW": gw,
            "was_home": "True" if home else "False",
            "opponent_team": opponent,
            "kickoff_time": kickoff,
            "team_h_score": scores[0],
            "team_a_score": scores[1],
            "total_points": points,
            "minutes": 90,
            "value": 55,
            "expected_goals": "",
            "influence": "12.4",
            "starts": "",
        }
    )
    return row


def season_rows():
    players = [
        player(1, 1),
        player(2, 2, element_type=4),
        player(3, 1, element_type=2),
        player(9, 1, element_type=5),
    ]
    players[2]["team"], players[2]["team_code"] = 2, 88  # where player 3 ended the season
    gws = [
        gw_row(1, 10, 1, home=True, opponent=2, kickoff="2016-08-13T14:00:00Z"),
        gw_row(2, 10, 1, home=False, opponent=1, kickoff="2016-08-13T14:00:00Z"),
        gw_row(3, 10, 1, home=True, opponent=2, kickoff="2016-08-13T14:00:00Z"),
        gw_row(9, 10, 1, home=True, opponent=2, kickoff="2016-08-13T14:00:00Z"),
        gw_row(1, 20, 2, home=False, opponent=2, kickoff="2016-08-20T11:30:00Z", scores=(0, 0)),
        gw_row(2, 20, 2, home=True, opponent=1, kickoff="2016-08-20T11:30:00Z", scores=(0, 0)),
        gw_row(3, 20, 2, home=True, opponent=1, kickoff="2016-08-20T11:30:00Z", scores=(0, 0)),
    ]
    return players, gws


def write_csv(path, rows, *, encoding="utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding=encoding) as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_season(
    raw, season="2016/17", *, players=None, gws=None, teams_csv=False, encoding="utf-8"
):
    default_players, default_gws = season_rows()
    folder = Path(raw) / season.replace("/", "-")
    write_csv(folder / "players_raw.csv", players or default_players)
    write_csv(folder / "gws" / "merged_gw.csv", gws or default_gws, encoding=encoding)
    if teams_csv:
        write_csv(
            folder / "teams.csv",
            [
                {"id": 1, "code": 3, "name": "Arsenal", "short_name": "ARS"},
                {"id": 2, "code": 88, "name": "Hull", "short_name": "HUL"},
            ],
        )
    master = Path(raw) / "master_team_list.csv"
    rows = []
    if master.exists():
        with master.open(newline="") as f:
            rows = list(csv.DictReader(f))
    rows += [
        {"season": folder.name, "team": 1, "team_name": "Arsenal"},
        {"season": folder.name, "team": 2, "team_name": "Hull"},
    ]
    write_csv(master, rows)
    return folder
