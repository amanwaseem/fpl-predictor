"""A manager's squad from the FPL API's public endpoints, for the advice route (#83).

    entry/{id}/                    started_event, current_event
    entry/{id}/event/{gw}/picks/   the fifteen, the bank, the chip played
    entry/{id}/history/            transfers made each gameweek, chips played
    entry/{id}/transfers/          every transfer, with what was paid

Responses are cached in Postgres for CACHE_SECONDS (fpl.db.models.UpstreamResponse),
so a manager asking twice, or many managers on a busy deadline day, do not
each cost a round of requests to an API that is undocumented and rate-limits.

Two things a manager's login would give exactly, the public endpoints only let
us reconstruct, and the response says so:

- **Selling prices.** FPL sells a player for his purchase price plus half of
  any rise, rounded down to £0.1m. The purchase price is the latest transfer in
  that was not undone by a free hit; for a player in the squad since the
  manager started, the price he had in the manager's first gameweek, from the
  stats loaded into the database.
- **Free transfers.** One after the first gameweek, one more each gameweek up
  to MAX_FREE_TRANSFERS, less those used; a wildcard or free hit uses none.
  Season-specific top-ups (2025/26's AFCON week) are not known, so the route
  takes an override.
"""

import os
from datetime import UTC, datetime, timedelta

import requests
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from fpl.db.models import Fixture, PlayerFixtureStats, UpstreamResponse
from fpl.optimise import MAX_FREE_TRANSFERS

BASE = os.environ.get("FPL_API_BASE", "https://fantasy.premierleague.com/api")
CACHE_SECONDS = 600
TIMEOUT = 10
HEADERS = {"User-Agent": "fpl-predictor (+https://github.com/amanwaseem/fpl-predictor)"}
# Chips that leave the free transfers as they were.
FREE_CHIPS = frozenset({"wildcard", "freehit"})


def fetch(engine, path):
    """The JSON at BASE/path, from the cache if it is fresh enough.

    A 404 from FPL is the caller's (no such team, or no picks for that
    gameweek), and is raised as one, and kept like an answer; anything else
    that goes wrong upstream is a 502, since the request itself was fine.
    """
    now = datetime.now(UTC)
    with engine.connect() as connection:
        row = connection.execute(
            select(UpstreamResponse.status, UpstreamResponse.body,
                   UpstreamResponse.fetched_at_utc)
            .where(UpstreamResponse.path == path)).first()
    if row is not None and now - row.fetched_at_utc < timedelta(seconds=CACHE_SECONDS):
        status, body = row.status, row.body
    else:
        status, body = _get(path)
        with engine.begin() as connection:
            values = {"status": status, "body": body, "fetched_at_utc": now}
            connection.execute(insert(UpstreamResponse).values(path=path, **values)
                               .on_conflict_do_update(index_elements=["path"], set_=values))
            # Kept small: anything older than a day will never be served again.
            connection.execute(UpstreamResponse.__table__.delete().where(
                UpstreamResponse.fetched_at_utc < now - timedelta(days=1)))
    if status == 404:
        raise HTTPException(404, f"The FPL API has nothing at {path}.")
    return body


def _get(path):
    """(200, body) or (404, None) from the FPL API; HTTPException(502) otherwise."""
    try:
        response = requests.get(f"{BASE}/{path}", headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException:
        raise HTTPException(502, "The FPL API did not answer.") from None
    if response.status_code == 404:
        return 404, None
    if response.status_code != 200:
        raise HTTPException(502, f"The FPL API answered {response.status_code}.")
    try:
        return 200, response.json()
    except ValueError:
        raise HTTPException(502, "The FPL API's answer was not JSON.") from None


def free_transfers(history, chips, through):
    """The free transfers a manager has for the gameweek after `through`.

    `history` is entry/{id}/history/'s `current`, `chips` its `chips`.
    """
    played = {c["event"]: c["name"] for c in chips}
    rows = sorted((r for r in history if r["event"] <= through), key=lambda r: r["event"])
    if not rows:
        return 1
    free = 1  # after the manager's first gameweek, whose transfers are unlimited
    for row in rows[1:]:
        if played.get(row["event"]) not in FREE_CHIPS:
            free = max(0, free - row["event_transfers"])
        free = min(MAX_FREE_TRANSFERS, free + 1)
    return free


def selling_price(purchase, current):
    """FPL's rule, in tenths: half of any rise, rounded down; all of any fall."""
    if current <= purchase:
        return current
    return purchase + (current - purchase) // 2


def purchase_prices(squad, transfers, chips, through):
    """{element: tenths paid} for the squad's players bought by transfer.

    Transfers made in a free hit gameweek are undone at the next deadline, so
    they bought nothing.
    """
    free_hits = {c["event"] for c in chips if c["name"] == "freehit"}
    paid = {}
    for t in sorted(transfers, key=lambda t: t["time"]):
        if t["event"] <= through and t["event"] not in free_hits and t["element_in"] in squad:
            paid[t["element_in"]] = t["element_in_cost"]
    return paid


def first_prices(engine, season, elements, gameweek):
    """{element: tenths} each player cost in `gameweek`, or his first gameweek after it."""
    with engine.connect() as connection:
        rows = connection.execute(
            select(PlayerFixtureStats.element_id, PlayerFixtureStats.value)
            .join(Fixture, (Fixture.season == PlayerFixtureStats.season)
                  & (Fixture.fixture_id == PlayerFixtureStats.fixture_id))
            .where(PlayerFixtureStats.season == season,
                   PlayerFixtureStats.element_id.in_(elements),
                   Fixture.gameweek >= gameweek)
            .order_by(PlayerFixtureStats.element_id, Fixture.gameweek)
            .distinct(PlayerFixtureStats.element_id))
        return dict(rows.all())


def manager_squad(engine, season, team_id, gameweek, prices):
    """What the optimiser needs to plan `gameweek` for FPL team `team_id`.

    `prices` maps element to the price in the entry being planned from, in £m.
    Returns (squad as {element: selling price in £m}, bank in £m, free
    transfers, the gameweek whose picks were used).
    """
    entry = fetch(engine, f"entry/{team_id}/")
    current, started = entry.get("current_event"), entry.get("started_event")
    if current is None or started is None or started >= gameweek:
        raise HTTPException(409, f"FPL team {team_id} has no squad from before GW{gameweek} "
                                 "to plan from.")
    through = min(current, gameweek - 1)
    picks = fetch(engine, f"entry/{team_id}/event/{through}/picks/")
    used = through
    # A free hit squad lasts one gameweek; the squad to plan from is the one
    # before it, which comes back at the next deadline.
    while picks.get("active_chip") == "freehit" and used > started:
        used -= 1
        picks = fetch(engine, f"entry/{team_id}/event/{used}/picks/")
    history = fetch(engine, f"entry/{team_id}/history/")
    transfers = fetch(engine, f"entry/{team_id}/transfers/")

    elements = [p["element"] for p in picks["picks"]]
    paid = purchase_prices(set(elements), transfers, history["chips"], through)
    original = first_prices(engine, season, [e for e in elements if e not in paid], started)
    squad = {}
    for element in elements:
        now = round(prices[element] * 10) if element in prices else None
        bought = paid.get(element, original.get(element, now))
        if now is None or bought is None:
            # Not in the entry: the optimiser says so, by id, as a 422.
            squad[element] = 0.1
            continue
        squad[element] = selling_price(bought, now) / 10
    bank = picks["entry_history"]["bank"] / 10
    return squad, bank, free_transfers(history["current"], history["chips"], through), used
