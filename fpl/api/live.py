"""Live points while a gameweek is played, from the FPL API (#199).

FPL publishes each player's points as matches go (`event/{gw}/live/`) and each
match's score and state (`fixtures/?event={gw}`). Both are read through the
API's upstream cache (fpl.api.upstream.fetch) with a max age of LIVE_SECONDS,
so however many people are watching, FPL gets at most one request a minute
for each.

Live points are provisional: bonus is FPL's own, added once it confirms it,
and automatic substitutions come after the gameweek. Official scoring
(scores/) is untouched; it still waits for a settled snapshot.
"""

from fastapi import HTTPException
from sqlalchemy import select

from fpl.api.upstream import fetch
from fpl.db.models import Team

LIVE_SECONDS = 60


def state(fixture):
    """not_started, live or finished. Finished once FPL says the match is over,
    before its bonus is confirmed (`finished` waits for that)."""
    if fixture.get("finished") or fixture.get("finished_provisional"):
        return "finished"
    return "live" if fixture.get("started") else "not_started"


def summarise(gameweek, live, fixtures, names, picks=None, team_id=None):
    """The /v1/live response from FPL's JSON. `names` maps FPL team id to short name."""
    try:
        matches = sorted(
            (
                {
                    "fixture_id": f["id"],
                    "kickoff_utc": f.get("kickoff_time"),
                    "home": names.get(f["team_h"], str(f["team_h"])),
                    "away": names.get(f["team_a"], str(f["team_a"])),
                    "home_score": f.get("team_h_score"),
                    "away_score": f.get("team_a_score"),
                    "state": state(f),
                    "minutes": f.get("minutes") or 0,
                }
                for f in fixtures
            ),
            key=lambda m: (m["kickoff_utc"] or "~", m["fixture_id"]),
        )
        players = [
            {
                "player_id": e["id"],
                "minutes": e["stats"]["minutes"],
                "total_points": e["stats"]["total_points"],
                "bonus": e["stats"]["bonus"],
            }
            for e in live["elements"]
        ]
        team = None if picks is None else _team(team_id, picks, players)
    # The API's schema has changed: the request was fine, FPL's answer wasn't.
    except (KeyError, TypeError, ValueError):
        raise HTTPException(502, "The FPL API's live data was not in the expected shape.") from None
    return {
        "gameweek": gameweek,
        "in_play": any(m["state"] == "live" for m in matches),
        "fixtures": matches,
        "players": players,
        "team": team,
    }


def _team(team_id, picks, players):
    """A manager's picks for the gameweek and their points so far."""
    points = {p["player_id"]: p["total_points"] for p in players}
    chosen = [
        {
            "player_id": p["element"],
            "position": p["position"],
            "multiplier": p["multiplier"],
            "is_captain": p["is_captain"],
            "is_vice_captain": p["is_vice_captain"],
        }
        for p in picks["picks"]
    ]
    cost = (picks.get("entry_history") or {}).get("event_transfers_cost") or 0
    return {
        "fpl_team_id": team_id,
        "active_chip": picks.get("active_chip"),
        "picks": chosen,
        "transfers_cost": cost,
        "points": sum(points.get(p["player_id"], 0) * p["multiplier"] for p in chosen) - cost,
    }


def team_names(engine, season):
    """{FPL team id: short name} for the season, from the database, or from FPL
    when the database has no football data (a log-only projection)."""
    with engine.connect() as connection:
        names = dict(
            connection.execute(
                select(Team.team_id, Team.short_name).where(Team.season == season)
            ).all()
        )
    if names:
        return names
    teams = fetch(engine, "bootstrap-static/").get("teams") or []
    return {t["id"]: t["short_name"] for t in teams if "id" in t and "short_name" in t}


def live(engine, season, gameweek, team_id=None):
    """Live points and scores for `gameweek`, and `team_id`'s picks if given."""
    elements = fetch(engine, f"event/{gameweek}/live/", max_age=LIVE_SECONDS)
    fixtures = fetch(engine, f"fixtures/?event={gameweek}", max_age=LIVE_SECONDS)
    # A manager's picks are fixed at the deadline: the usual cache age.
    picks = None if team_id is None else fetch(engine, f"entry/{team_id}/event/{gameweek}/picks/")
    return {"season": season} | summarise(
        gameweek, elements, fixtures, team_names(engine, season), picks, team_id
    )
