# CLAUDE.md

Context for agentic work in this repository. Read `SPEC.md` for full requirements.

## What this is

An FPL points predictor with an append-only public prediction log. Predictions
are committed **before** each gameweek deadline and scored afterwards. The log
is the credibility mechanism — it makes the model's performance falsifiable.

## Hard rules

These are not style preferences. Breaking them destroys the point of the project.

1. **Never rewrite a committed prediction.** Files in `predictions/` are
   immutable once pushed. If a prediction was wrong, it stays wrong and gets
   scored as wrong. No amended files, no force pushes touching that directory.

2. **Never use post-deadline information to predict a gameweek.** When building
   features for GW N, use only data from gameweeks strictly before N. Any
   accidental leak invalidates the whole track record.

3. **Never commit raw data.** `data/raw/` is gitignored. Snapshots are large and
   regenerable via `fetch_fpl.py`.

4. **Don't push to `main`.** Work on branches, open PRs.

## Current state

Early development, 2026/27 season.

- `fetch_fpl.py` — working. Snapshots the FPL API to `data/raw/<timestamp>/`.
- `predict_baseline.py` — in progress. Rolling-form baseline.
- Everything else — not built. See `SPEC.md`.

## Timeline

The season is a hard external constraint. The log only accumulates evidence
while the season runs.

- **GW4 deadline: 2026-09-12T12:30:00Z** — first prediction log entry due
- GW5 deadline: 2026-09-18T17:30:00Z
- International break: no fixtures 26 Sep / 3 Oct
- GW6: 2026-10-10T10:00:00Z

Anything not needed for a committed GW4 prediction is post-break work. Do not
build infrastructure at the expense of the deadline.

## Data source

The unofficial FPL API at `https://fantasy.premierleague.com/api/`. Undocumented,
unversioned, unsupported. The schema can change without notice — this is why
ingestion is snapshot-first rather than live-fetched at prediction time.

Key endpoints: `bootstrap-static/`, `fixtures/`, `element-summary/{id}/`,
`event/{id}/live/`, `event-status/`.

## Conventions

- Python 3.13, standard library plus `requests`. Add dependencies only when
  genuinely needed and pin them in `requirements.txt`.
- All timestamps UTC. FPL deadlines are published in UTC and timezone ambiguity
  near a deadline-sensitive system is unacceptable.
- Gameweek filenames zero-padded: `gw04`, not `gw4`.
- Prefer explicit and readable over clever. This repo is read by humans
  evaluating the author.

## When something is ambiguous

Ask rather than guess. In particular, do not "fix" a failing script by removing
the failing part, and do not silently change model parameters — those are
decisions with consequences for the log.
