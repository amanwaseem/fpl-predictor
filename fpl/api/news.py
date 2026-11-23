"""Team news from the FPL API: who may miss the next gameweek (#196).

The database loads a snapshot once a day, but FPL updates news through the
day, and a doubt is only worth showing while it is current. So this reads
bootstrap-static through the API's cache (fpl.api.upstream.fetch), as the
advice route reads a manager's squad, rather than keeping news in Postgres.

Listed: doubtful, injured, suspended and not available. A player who has left
the league ("u") is not news for a manager. Most owned first, so the doubts
that touch most teams lead.
"""

from fastapi import HTTPException

from fpl.db.load import POSITIONS

# FPL's status codes for a player who may miss the next gameweek.
DOUBTS = ("d", "i", "s", "n")


def doubts(bootstrap):
    """Players in bootstrap-static who may miss the next gameweek, most owned first."""
    try:
        teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
        items = [
            {
                "player_id": e["id"],
                "player_code": e.get("code"),
                "web_name": e["web_name"],
                "team": teams[e["team"]],
                "position": POSITIONS[e["element_type"]],
                "status": e["status"],
                "chance_of_playing": e.get("chance_of_playing_next_round"),
                "news": e.get("news") or "",
                "news_added_utc": e.get("news_added"),
                "selected_by_percent": float(e.get("selected_by_percent") or 0),
            }
            for e in bootstrap["elements"]
            if e.get("status") in DOUBTS
        ]
    # The API's schema has changed: the request was fine, FPL's answer wasn't.
    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            502, "The FPL API's player list was not in the expected shape."
        ) from None
    return sorted(items, key=lambda i: (-i["selected_by_percent"], i["web_name"]))
