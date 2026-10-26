# 0018. Past seasons are training data, from a pinned archive

- **Status:** Accepted
- **Date:** 2026-10-25
- **Issues:** #79, #84, #85, #75

## Context

Every model so far is fitted on the current season's settled gameweeks:
about 2,500 player-gameweek rows by GW6. That is too few to learn anything,
or to tell two models apart: held-out differences of 0.002 MAE are noise. A
learned model (#85) needs the seasons before this one, and the FPL API
serves only the current season.

[vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League)
archives the API's per-gameweek data for every season since 2016/17. It is
a third party's repository: it can be rewritten, and its files differ from
season to season in columns, encoding and completeness.

## Decision

- **Past seasons are training data, never entries.** 2016/17 to 2025/26 load
  into the same tables as the live season, each under its own `season`. The
  log holds 2026/27 entries only, each made before its deadline (SPEC §2).
  Nothing about a past season is evidence of how a model predicts.
- **The archive is pinned.** One commit, and every file's SHA-256, in
  `fpl/ingest/history.py`. A file that does not match is refused. Downloads
  live under `data/raw/history/<commit>/`, gitignored like snapshots (0002).
  Moving to a newer commit is a reviewed change to that module, with the new
  hashes and counts.
- **Loaded counts are pinned too.** `fpl.db.backfill.EXPECTED` holds each
  season's players, fixtures and player-fixture rows. A load that differs is
  rolled back, and CI's db job loads all ten seasons and checks them.
- **People are joined by FPL's code.** Element ids restart every season; a
  player's `code` does not, so `season_players.player_code` links a career.
- **The live season is never backfilled.** A season with snapshots loaded is
  refused: the snapshot loader keeps it.
- **What the archive lacks is reconstructed, and said.** Fixtures come from
  the player rows (both sides of every fixture have players); deadlines are
  90 minutes before each gameweek's first kick-off, the earlier of FPL's two
  rules; 2019/20's restart, which FPL numbered GW39 to GW47, is stored as
  GW30 to GW38; duplicate rows keep the one with the result.
- *Rejected:* scraping each past season from FPL's own API. It serves only
  the current season.

## Consequences

- About 254,000 player-fixture rows, 64 MB with the live season, within
  Neon's free tier.
- A reconstructed deadline can be up to 30 minutes earlier than the real one
  in seasons with a 60-minute rule. A feature cut at it loses at most news
  from that half hour, never gains any from after the deadline.
- Columns FPL added later (expected goals from 2022/23, defensive
  contribution from 2025/26) are null before them. A model that uses them
  has fewer seasons to learn from (#84 decides how).
- 2022/23's GW7, postponed whole after the Queen's death, has no fixtures and
  so no gameweek row.

## Revisit if

- The archive is taken down or its history rewritten. The pinned files are
  still on every machine that downloaded them, and the hashes say which.
- FPL starts serving past seasons itself.
