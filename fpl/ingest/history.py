"""Past seasons, 2016/17 to 2025/26, as training data (#79, ADR 0018).

    fpl-backfill --download-only     # the pinned files, into data/raw/history/
    fpl-backfill                     # every season, into the database

The source is vaastav/Fantasy-Premier-League, a public archive of the FPL
API's per-gameweek data, pinned to one commit and every file to its SHA-256.
A file that does not match is refused, so a season loaded today is the season
loaded next year. Downloads live under data/raw/history/<commit>/, gitignored
like every other snapshot (rule 3).

This module only reads and shapes the files; fpl.db.backfill writes them.
Each season becomes the same rows the snapshot loader writes for the current
one, under its own season, with three differences forced by the archive:

- **Fixtures come from the player rows.** The archive has no fixtures file
  before 2019/20. Every fixture has players on both sides, and a player's row
  names his opponent and whether he was at home, which is enough for the home
  side, the away side, the kick-off and the score.
- **Deadlines are reconstructed**: 90 minutes before the gameweek's first
  kick-off, FPL's rule for most of these seasons and the earlier of the two it
  has used. Earlier is the safe side for a feature that must only use what
  was known at the deadline. A gameweek with no fixtures still gets a row,
  as the live loader's do, its deadline between its neighbours'.
- **2019/20's restart is renumbered.** FPL numbered the gameweeks after the
  2020 suspension 39 to 47, leaving 30 to 38 empty. They are stored as 30 to
  38: the order is the same, and a gameweek is between 1 and 38 everywhere.
"""

import csv
import hashlib
import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import requests
from sqlalchemy import Boolean, Integer, SmallInteger

from fpl.db.load import (
    LAST_GAMEWEEK,
    POSITIONS,
    club_spells,
    season_player_rows,
    stats_row,
    team_rows,
)
from fpl.db.models import PlayerFixtureStats

SOURCE = "vaastav/Fantasy-Premier-League"
COMMIT = "d7b05b51d137bfefef4b692386d18c610975b9b9"
RAW = Path("data/raw/history") / COMMIT
URL = f"https://raw.githubusercontent.com/{SOURCE}/{COMMIT}/data"
TIMEOUT = 60

SEASONS = [
    "2016/17",
    "2017/18",
    "2018/19",
    "2019/20",
    "2020/21",
    "2021/22",
    "2022/23",
    "2023/24",
    "2024/25",
    "2025/26",
]

FILES = {
    "master_team_list.csv": "c67678853a78f0488125f8d10982eae2265d4e2d83aa1ed3656335eda42992bf",
    "2016-17/players_raw.csv": "824183cf6c00db4c476706112230a16a8135f5dee82240f56df14547ad22db84",
    "2016-17/gws/merged_gw.csv": "49fd5f815509513fac8cf329abc0faa4e48d99ab57b56162220bcb6caeae4116",
    "2017-18/players_raw.csv": "c40f7e6a74160f3a151fb32ee79d5843e87daf58e1a002ccfff412a224804264",
    "2017-18/gws/merged_gw.csv": "1d8288bad9994994070100b23a4fc2b28efeb79d8be13c9e6f6be559c1c9369d",
    "2018-19/players_raw.csv": "a2ad425f5692842d05ddc8de7eafa63cece551fde788278427594790e982046a",
    "2018-19/gws/merged_gw.csv": "0c96ab3e3b12ffa9d27e24483b494cc9350d24f02520f2c964ea8b8f19f08c68",
    "2019-20/players_raw.csv": "6ebf73e7f4fe0d697869a12cbf39ccb1df444c6b9b1be6c02e61677ebbfc243f",
    "2019-20/gws/merged_gw.csv": "750dbc03cab3f894152173f6e0a42d1cfa86efd6d8dab7e9b5c6c608c93b72cc",
    "2019-20/teams.csv": "fbe8ae4036e5cb9e05aba4eb029da73036c76e66ee276b007e8296072af11b22",
    "2020-21/players_raw.csv": "e3aff899fc2486bd96fe5d11d8c8e8c066dea36cc71de2bf8a85e8aa7245ed32",
    "2020-21/gws/merged_gw.csv": "fb2a2bc54b6cfbe835a39183b096b382f7961e3828e7845e9288671cccca3ad8",
    "2020-21/teams.csv": "0e3b9fdad72feefb88db3b848e4d579020327f4bf9975fa82fb2085dbe7f0fae",
    "2021-22/players_raw.csv": "78e185fb0b5c81f58c2b89f232358b1097bededc9852d4171edaea2e9d3bd887",
    "2021-22/gws/merged_gw.csv": "29770bbe305feee681f7122c0bd08451783366e0a3a9604194f528a0880f23de",
    "2021-22/teams.csv": "c1dd02ed25fe832ba7bf6ce846ac506a00a0f0babdacb6bf2a011dc9eaa8cfad",
    "2022-23/players_raw.csv": "a874c12817bbf4d454e60a5765629742b528d4ad0c2f39f56e4fc89640605f7f",
    "2022-23/gws/merged_gw.csv": "b78a6d0456141f9033c32fc4122931baae780ac4a4938683451b1fce7a4fdd15",
    "2022-23/teams.csv": "be6a1fe120a61f9c441a0751ca177c933bcbf234c1d28d49b3cd8827ca18066b",
    "2023-24/players_raw.csv": "43d8cf5efb3d901f2499558c40b392b7f0ee22afe28030f36266ad29024c63d9",
    "2023-24/gws/merged_gw.csv": "e9c09c8856f1c86b4f920f46ddd5033af83409439dfda53be925df2a3e7c8a9e",
    "2023-24/teams.csv": "7795f091ccea17fda0752cb07b935e1515b09a9d3f924147997cbf28139edd49",
    "2024-25/players_raw.csv": "75686051b265cbe7755ac71213ecaad21b26ee1cc46a8bafbba19c39ce894b05",
    "2024-25/gws/merged_gw.csv": "5bbbcba6353b4c72ad273adcc8e3aa451946a826564679788f45b1cb3325b84e",
    "2024-25/teams.csv": "d10a6db8feca6db9e44a3ba50b15168396667ea387fee9235d6b211c7f598c3b",
    "2025-26/players_raw.csv": "412ce0172016f8f98f25177dc6de9f3cd2a8ec7a6135f9aa638d7fdee784d67b",
    "2025-26/gws/merged_gw.csv": "0d09f1f1cb1b5520ec8e2f25238aa652efe2a263d8ca7cb2b6538b27bf86727d",
    "2025-26/teams.csv": "b29df099cb0ad25413e284e53116099b0e0496874f99743dbc0870d8241b46c5",
}

# Clubs whose only seasons here predate the archive's teams.csv (2019/20), so
# their short names come from here. Keyed by FPL's club code, which is stable
# across seasons; every other club's comes from a later teams.csv.
EARLY_CLUBS = {88: "HUL", 25: "MID", 110: "STK", 80: "SWA", 38: "HUD", 97: "CAR"}
DEADLINE_BEFORE_KICKOFF = timedelta(minutes=90)
# 2019/20: FPL's gameweeks after the restart, and where they are stored.
RESTART = {"2019/20": (39, 9)}


def folder(season):
    """'2016-17' for '2016/17', as the archive names its directories."""
    return season.replace("/", "-")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def download(raw=RAW):
    """Every pinned file into `raw`, checked against its hash. Returns the paths fetched."""
    fetched = []
    for name, digest in FILES.items():
        path = raw / name
        if path.exists() and sha256(path) == digest:
            continue
        response = requests.get(f"{URL}/{name}", timeout=TIMEOUT)
        response.raise_for_status()
        found = hashlib.sha256(response.content).hexdigest()
        if found != digest:
            raise SystemExit(
                f"{name} from {SOURCE}@{COMMIT[:7]} has SHA-256 {found}, "
                f"not the pinned {digest}. Not loading a file that changed."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix + ".part")
        partial.write_bytes(response.content)
        partial.replace(path)
        fetched.append(path)
    return fetched


def verify(raw=RAW):
    """Every pinned file is in `raw` with its hash, or SystemExit naming the first that is not."""
    for name, digest in FILES.items():
        path = raw / name
        if not path.exists():
            raise SystemExit(f"{path} is missing. Run: fpl-backfill --download-only")
        if sha256(path) != digest:
            raise SystemExit(
                f"{path} does not match its pinned SHA-256. Delete it and run "
                "fpl-backfill --download-only"
            )


def read_csv(path):
    """Rows as dicts. Three seasons' gameweek files are cp1252, not UTF-8."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    return list(csv.DictReader(io.StringIO(text)))


@dataclass
class Season:
    """One season's rows, keyed by table, ready for fpl.db.backfill."""

    season: str
    teams: list[dict] = field(default_factory=list)
    players: list[dict] = field(default_factory=list)
    season_players: list[dict] = field(default_factory=list)
    gameweeks: list[dict] = field(default_factory=list)
    fixtures: list[dict] = field(default_factory=list)
    player_clubs: list[dict] = field(default_factory=list)
    player_fixture_stats: list[dict] = field(default_factory=list)
    # What was dropped, and why: reported by the backfill, checked by the tests.
    duplicates: int = 0
    not_players: int = 0


def short_names(raw=RAW):
    """{club code: short name}, from every teams.csv in the archive and EARLY_CLUBS."""
    names = dict(EARLY_CLUBS)
    for season in SEASONS:
        path = raw / folder(season) / "teams.csv"
        if path.exists():
            names.update({int(t["code"]): t["short_name"] for t in read_csv(path)})
    return names


def timestamp(value):
    return datetime.fromisoformat(value) if value else None


def gameweek_of(season, round_):
    first, shift = RESTART.get(season, (None, 0))
    return round_ - shift if first is not None and round_ >= first else round_


def parse_season(season, raw=RAW, names=None):
    """A Season from the archive's files for `season`.

    Teams, season players and stats rows are built by fpl.db.load's own
    builders, from the archive's values typed as the API would send them, so
    a past season is shaped exactly as the live one is.
    """
    names = short_names(raw) if names is None else names
    base = raw / folder(season)
    elements = [_typed_element(e) for e in read_csv(base / "players_raw.csv")]
    players = {e["id"]: e for e in elements if e["element_type"] in POSITIONS}
    result = Season(season)

    # Teams: ids and codes from the players' file, names from teams.csv where
    # the season has one and the master list where it does not.
    codes = {e["team"]: e["team_code"] for e in elements}
    if (base / "teams.csv").exists():
        full = {int(t["id"]): t["name"] for t in read_csv(base / "teams.csv")}
    else:
        full = {
            int(t["team"]): t["team_name"]
            for t in read_csv(raw / "master_team_list.csv")
            if t["season"] == folder(season)
        }
    bootstrap = {
        "teams": [
            {"id": tid, "code": code, "name": full[tid], "short_name": names[code]}
            for tid, code in sorted(codes.items())
        ]
    }
    result.teams = team_rows(season, bootstrap)
    result.players = [
        {"code": e["code"], "first_name": e["first_name"], "second_name": e["second_name"]}
        for e in players.values()
    ]
    result.season_players = season_player_rows(season, [players[p] for p in sorted(players)])

    rows = []
    for row in read_csv(base / "gws" / "merged_gw.csv"):
        if int(row["element"]) not in players:
            result.not_players += 1
            continue
        rows.append(row | {"round": gameweek_of(season, int(row["round"]))})
    kept = _deduplicated(season, rows, result)

    result.fixtures = _fixtures(season, kept)
    result.gameweeks = _gameweeks(season, result.fixtures)

    fixtures_by_id = {
        f["fixture_id"]: {"team_h": f["team_h"], "team_a": f["team_a"]} for f in result.fixtures
    }
    typed = [_typed_row(row) for row in kept]
    history = defaultdict(list)
    for row in typed:
        history[row["element"]].append(row)
    for pid, e in sorted(players.items()):
        for team, gameweeks in club_spells(history[pid], fixtures_by_id, e["team"], None):
            result.player_clubs.append(
                {"season": season, "element_id": pid, "team_id": team, "gameweeks": gameweeks}
            )
    result.player_fixture_stats = [stats_row(season, None, row) for row in typed]
    return result


def _gameweeks(season, fixtures):
    """Every gameweek, 1 to 38. A deadline is 90 minutes before the first kick-off;
    a gameweek with no fixtures (2022/23's GW7, postponed whole) has none to go by,
    so it is put midway between its neighbours'."""
    kickoffs = defaultdict(list)
    for f in fixtures:
        kickoffs[f["gameweek"]].append(f["kickoff_utc"])
    deadlines = {gw: min(k) - DEADLINE_BEFORE_KICKOFF for gw, k in kickoffs.items()}
    if not deadlines:
        return []
    for gw in range(1, LAST_GAMEWEEK + 1):
        if gw in deadlines:
            continue
        before = max((g for g in deadlines if g < gw), default=None)
        after = min((g for g in deadlines if g > gw), default=None)
        if before is not None and after is not None:
            share = (gw - before) / (after - before)
            deadlines[gw] = deadlines[before] + (deadlines[after] - deadlines[before]) * share
        elif before is not None:
            deadlines[gw] = deadlines[before] + timedelta(weeks=gw - before)
        else:
            deadlines[gw] = deadlines[after] - timedelta(weeks=after - gw)
    return [
        {
            "season": season,
            "gameweek": gw,
            "finished": True,
            "data_checked": True,
            "deadline_utc": deadlines[gw],
        }
        for gw in range(1, LAST_GAMEWEEK + 1)
    ]


def _deduplicated(season, rows, result):
    """One row per (element, fixture). Where the archive has two, they must be the
    same row twice, or, in 2019/20, a fixture postponed in March and played in
    June, whose stale row has no score: the row with the result is kept. Any
    other pair is refused, rather than one of them kept by file order."""
    best = {}
    for row in rows:
        key = (row["element"], row["fixture"])
        if key not in best:
            best[key] = row
            continue
        result.duplicates += 1
        kept, scored = best[key], row["team_h_score"] != ""
        if row == kept or not scored:
            continue
        if kept["team_h_score"] == "":
            best[key] = row
            continue
        raise SystemExit(
            f"{season}: two different rows for element {key[0]} in fixture "
            f"{key[1]}, both with a result. Not choosing between them."
        )
    return list(best.values())


def _fixtures(season, rows):
    """Fixtures from the player rows, every row of a fixture agreeing about it."""
    sides = defaultdict(dict)
    facts = {}
    for row in rows:
        fid = int(row["fixture"])
        fixture = sides[fid]
        # A home player's opponent is the away side, and the other way round.
        fixture["team_a" if row["was_home"] == "True" else "team_h"] = int(row["opponent_team"])
        fact = (row["kickoff_time"], row["round"], row["team_h_score"], row["team_a_score"])
        if facts.setdefault(fid, fact) != fact:
            raise SystemExit(
                f"{season} fixture {fid}: player rows disagree about its kick-off, "
                f"gameweek or score ({facts[fid]} against {fact})."
            )
    fixtures = []
    for fid, f in sorted(sides.items()):
        if "team_h" not in f or "team_a" not in f:
            raise SystemExit(f"{season} fixture {fid} has players on one side only.")
        kickoff, gameweek, home, away = facts[fid]
        if home == "" or away == "":
            raise SystemExit(f"{season} fixture {fid} has no result.")
        fixtures.append(
            {
                "season": season,
                "fixture_id": fid,
                "gameweek": gameweek,
                "kickoff_utc": timestamp(kickoff),
                "team_h": f["team_h"],
                "team_a": f["team_a"],
                "team_h_score": int(home),
                "team_a_score": int(away),
                "finished": True,
            }
        )
    return fixtures


def _typed_element(e):
    """A players_raw row with the bootstrap's types."""
    return e | {key: int(e[key]) for key in ("id", "code", "element_type", "team", "team_code")}


def _typed_row(row):
    """A merged_gw row as an element-summary history row: the API's types, with
    numbers it sends as strings (expected goals, ICT) left as strings for
    stats_row, and an empty cell as no value."""
    typed = {}
    for key, value in row.items():
        column = STAT_COLUMNS.get(key)
        if value == "":
            typed[key] = None
        elif column is not None and isinstance(column.type, Boolean):
            typed[key] = value == "True"
        elif column is not None and isinstance(column.type, Integer | SmallInteger):
            typed[key] = int(float(value))
        else:
            typed[key] = value
    typed["element"], typed["fixture"] = int(row["element"]), int(row["fixture"])
    typed["was_home"] = row["was_home"] == "True"
    return typed


STAT_COLUMNS = {c.name: c for c in PlayerFixtureStats.__table__.columns}
