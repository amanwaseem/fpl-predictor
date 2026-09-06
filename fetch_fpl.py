"""Snapshot raw FPL API data to disk.

Writes an immutable, timestamped snapshot so every prediction can be traced
back to exactly the data it was produced from.

Usage:
    python fetch_fpl.py                 # full snapshot (slow, ~6 min)
    python fetch_fpl.py --skip-players  # bootstrap + fixtures only (fast)
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://fantasy.premierleague.com/api"
DELAY = 0.5  # be polite; this is an undocumented public API
TIMEOUT = 20
RETRIES = 3


def get(path):
    """GET a JSON endpoint with simple retry/backoff."""
    url = f"{BASE}/{path}"
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "fpl-predictor/0.1"})
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == RETRIES:
                raise
            wait = 2 ** attempt
            print(f"  retry {attempt}/{RETRIES} for {path} after {wait}s ({e})")
            time.sleep(wait)


def write(outdir: Path, name: str, payload) -> None:
    path = outdir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def main(skip_players: bool) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = Path("data/raw") / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"snapshot -> {outdir}")

    print("fetching bootstrap-static ...")
    bootstrap = get("bootstrap-static/")
    write(outdir, "bootstrap.json", bootstrap)

    players = bootstrap["elements"]
    events = bootstrap["events"]
    print(f"  {len(players)} players, {len(bootstrap['teams'])} teams")

    current = next((e for e in events if e.get("is_current")), None)
    upcoming = next((e for e in events if e.get("is_next")), None)
    if current:
        print(f"  current gameweek: GW{current['id']}")
    if upcoming:
        print(f"  next gameweek: GW{upcoming['id']}  deadline: {upcoming['deadline_time']}")

    print("fetching fixtures ...")
    write(outdir, "fixtures.json", get("fixtures/"))

    if skip_players:
        print("skipping per-player history")
    else:
        print(f"fetching per-player history for {len(players)} players ...")
        for i, p in enumerate(players, start=1):
            pid = p["id"]
            write(outdir, f"players/{pid}.json", get(f"element-summary/{pid}/"))
            if i % 50 == 0:
                print(f"  {i}/{len(players)}")
            time.sleep(DELAY)

    # Stable pointer to the most recent snapshot, so downstream code
    # doesn't need to guess at timestamps.
    Path("data/raw/LATEST").write_text(stamp)
    print(f"done: {outdir}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-players", action="store_true", help="skip slow per-player fetch")
    main(ap.parse_args().skip_players)
