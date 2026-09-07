"""Snapshot raw FPL API data to disk.

Writes an immutable, timestamped snapshot so every prediction can be traced
back to exactly the data it was produced from.

A snapshot is only usable once manifest.json exists. That file is written
last, so an interrupted fetch leaves a directory that is visibly incomplete
rather than one that merely looks finished. Re-running resumes: player files
already on disk are not refetched.

Usage:
    python fetch_fpl.py                 # full snapshot (slow, ~6 min)
    python fetch_fpl.py --skip-players  # bootstrap + fixtures only (fast)
    python fetch_fpl.py                 # re-run to resume an interrupted fetch
"""

import argparse
import json
import os
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
    """Write JSON atomically.

    A partial write is worse than no write: resume trusts exists(), so a
    0-byte file left by a Ctrl-C mid-write would be skipped as "already
    fetched" and then blessed by the manifest. Write to a temp file in the
    same directory and rename, which is atomic on POSIX.
    """
    path = outdir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def find_incomplete():
    """Most recent interrupted full fetch, or None.

    Deliberately narrow. A manifest-less directory is not automatically
    resumable: a bootstrap-only snapshot taken days ago has no players/ at
    all, and continuing it would pair today's player histories with a stale
    bootstrap — the temporal mixing resume exists to avoid. Only a directory
    that already holds at least one player file represents a full fetch that
    was interrupted partway.

    Anything at or older than LATEST is skipped too: a complete newer snapshot
    already exists, so finishing an older one gains nothing and risks pointing
    the predictor at staler data.
    """
    root = Path("data/raw")
    if not root.exists():
        return None

    latest_file = root / "LATEST"
    latest = latest_file.read_text().strip() if latest_file.exists() else ""

    candidates = [
        d for d in root.iterdir()
        if d.is_dir()
        and not (d / "manifest.json").exists()
        and (d / "players").is_dir()
        and any((d / "players").iterdir())
        and d.name > latest
    ]
    return max(candidates, key=lambda d: d.name) if candidates else None


def main(skip_players: bool, resume: bool) -> None:
    if resume:
        outdir = find_incomplete()
        if outdir is None:
            raise SystemExit("Nothing to resume: every snapshot has a manifest.")
        stamp = outdir.name
        print(f"resuming -> {outdir}")
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        outdir = Path("data/raw") / stamp
        print(f"snapshot -> {outdir}")
    outdir.mkdir(parents=True, exist_ok=True)

    players_dir = outdir / "players"
    partial = players_dir.is_dir() and any(players_dir.iterdir())
    if skip_players and partial:
        raise SystemExit(
            f"{outdir} already holds partial player history.\n"
            "--skip-players would mark it complete while players are still "
            "missing, and the resulting snapshot would look usable.\n"
            "Drop --skip-players to finish it."
        )


    # On resume, reuse what is already on disk. Refetching bootstrap or
    # fixtures would mix data from two points in time into one snapshot,
    # which defeats the purpose of snapshotting at all.
    bootstrap_path = outdir / "bootstrap.json"
    if resume and bootstrap_path.exists():
        print("reusing bootstrap-static from disk")
        bootstrap = json.loads(bootstrap_path.read_text())
    else:
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

    if resume and (outdir / "fixtures.json").exists():
        print("reusing fixtures from disk")
    else:
        print("fetching fixtures ...")
        write(outdir, "fixtures.json", get("fixtures/"))

    if skip_players:
        print("skipping per-player history")
    else:
        print(f"fetching per-player history for {len(players)} players ...")
        fetched = skipped = 0
        for i, p in enumerate(players, start=1):
            pid = p["id"]
            # Resume: a six-minute fetch that dies at 80% should not restart.
            if (outdir / "players" / f"{pid}.json").exists():
                skipped += 1
                continue
            write(outdir, f"players/{pid}.json", get(f"element-summary/{pid}/"))
            fetched += 1
            if i % 50 == 0:
                print(f"  {i}/{len(players)}")
            time.sleep(DELAY)
        if skipped:
            print(f"  resumed: {skipped} already on disk, {fetched} fetched")

        missing = [p["id"] for p in players
                   if not (outdir / "players" / f"{p['id']}.json").exists()]
        if missing:
            raise SystemExit(
                f"\n{len(missing)} player files missing after fetch. "
                "No manifest written, so this snapshot cannot be used.\n"
                "Re-run to resume."
            )

    # Written last, and only on success. Its absence is what marks a snapshot
    # as unusable — a half-finished directory is otherwise indistinguishable
    # from a complete one, and a partial snapshot silently produces a partial
    # prediction log that hard rule 1 then makes permanent.
    write(outdir, "manifest.json", {
        "snapshot_id": stamp,
        "element_count": len(players),
        "has_players": not skip_players,
        "completed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })

    # Stable pointer to the most recent snapshot, so downstream code doesn't
    # need to guess at timestamps. Only a full snapshot earns the pointer:
    # otherwise one fast bootstrap-only fetch destroys the reference to the
    # last snapshot that was actually usable for prediction.
    latest = Path("data/raw/LATEST")
    current = latest.read_text().strip() if latest.exists() else ""
    if skip_players:
        print(f"done (bootstrap only): {outdir}")
        print("LATEST unchanged — bootstrap-only snapshots cannot be predicted from.")
    elif stamp < current:
        # Timestamps sort lexicographically, so this is a real ordering.
        # Resuming an old interrupted snapshot must never drag the pointer
        # backwards onto staler data than the predictor already has.
        print(f"done: {outdir}")
        print(f"LATEST unchanged — {current} is newer than this snapshot.")
    else:
        latest.write_text(stamp)
        print(f"done: {outdir}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-players", action="store_true", help="skip slow per-player fetch")
    ap.add_argument("--resume", action="store_true",
                    help="continue the most recent snapshot that has no manifest")
    args = ap.parse_args()
    main(args.skip_players, args.resume)
