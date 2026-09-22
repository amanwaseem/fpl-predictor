# CLAUDE.md

Context for agentic work in this repository. Read `SPEC.md` for full requirements.

This file holds the things that stay true all season. It deliberately carries no
module inventory and no dated timeline — those go stale weekly. For what exists,
read the code and `SPEC.md`; for what is planned, read the open issues; for how
to produce a log entry, follow `RUNBOOK.md`.

## What this is

An FPL points predictor with an append-only public prediction log. Predictions
are committed **before** each gameweek deadline and scored afterwards. The log
is the credibility mechanism — it makes the model's performance falsifiable.

## Hard rules

Only these three are non-negotiable. Breaking them destroys the point of the project.

1. **Never rewrite a committed prediction.** Files in `predictions/` are
   immutable once pushed. If a prediction was wrong, it stays wrong and gets
   scored as wrong. No amended files, no force pushes touching that directory.
   This applies to `predictions/` and nothing else — `scores/` and `reports/`
   are derived output and are meant to be rewritten when a definition changes.

2. **Never use post-deadline information to predict a gameweek.** When building
   features for GW N, use only data from gameweeks strictly before N. A missed
   deadline stays missed — a late entry is not evidence of anything.

3. **Never commit raw data.** `data/raw/` is gitignored. Snapshots are large and
   regenerable via `fpl/fetch.py`.

## Working norms

Defaults, not rules. Use judgement.

- Work on branches and open PRs rather than pushing to `main`.
- The season is the one external constraint: during a gameweek week, the next
  entry comes first. Between deadlines, build freely.
- New models import from `fpl.snapshot`, `fpl.features` and `fpl.log`, not from
  `predict_baseline` — the baseline is meant to be beaten and deleted.
- `RUNBOOK.md` is the procedure for an entry. If it is wrong, fix it alongside
  the entry it misled you on.
- Run things with `.venv/bin/python` (or an activated `.venv`).

## Data source

The unofficial FPL API at `https://fantasy.premierleague.com/api/`. Undocumented,
unversioned, unsupported. The schema can change without notice — this is why
ingestion is snapshot-first rather than live-fetched at prediction time.

Take snapshots between gameweeks rather than during one: a mid-gameweek snapshot
carries zeroed history rows for fixtures that have not kicked off, and bonus is
provisional until `data_checked` is true. `predict_baseline` excludes unsettled
rounds and prints which it used — read those lines before trusting a run.

## Conventions

- Python 3.13, standard library plus `requests`. Add dependencies when they earn
  their place and pin them in `requirements.txt`.
- All timestamps UTC.
- Gameweek filenames zero-padded: `gw04`, not `gw4`.
- `scratch/` is gitignored space for exploratory runs. Never put a real
  prediction there, and never put an exploratory run in `predictions/`.
- Prefer explicit and readable over clever. This repo is read by humans
  evaluating the author.

## When something is ambiguous

Make a sensible call on small things and say what you chose. Ask first on
anything that touches the log — model parameters feeding a committed entry,
anything under `predictions/`, or "fixing" a failing check by removing it.
