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
   This applies to `predictions/` and nothing else — `scores/` is derived
   output, recomputable from the committed predictions plus a snapshot, and is
   meant to be rewritten when a metric definition changes. Over-applying the
   rule there would freeze the metrics instead of the evidence.

2. **Never use post-deadline information to predict a gameweek.** When building
   features for GW N, use only data from gameweeks strictly before N. Any
   accidental leak invalidates the whole track record.

3. **Never commit raw data.** `data/raw/` is gitignored. Snapshots are large and
   regenerable via `fpl/fetch.py`.

4. **Don't push to `main`.** Work on branches, open PRs.

## Current state

Early development, 2026/27 season.

- `fpl/fetch.py` — working. Snapshots the FPL API to `data/raw/<timestamp>/`.
- `fpl/predict_baseline.py` — runs end to end against a full snapshot. Untuned.
  Takes `--out DIR` (defaults to `predictions/`); use `--out scratch/` for
  exploratory runs.
- `fpl/snapshot.py` — loading a snapshot and bounding what may be read from it.
  Both rule 2 guards live here: `resolve_target_gw` refuses to backtest from a
  later snapshot, `usable_rounds` drops rounds whose results are not final.
  `resolve_target_gw` also refuses to predict *past* the snapshot's own next
  gameweek, where `chance_of_playing_next_round` would be scoped to the wrong
  one — a snapshot predicts its own next gameweek and nothing else.
  `load_live` reads back actual points for a settled gameweek.
- `fpl/features.py` — snapshot rows to model inputs. Owns the form window.
- `fpl/log.py` — the log schema and the append-only write. Rule 1 lives here.
- `fpl/verify_entry.py` — checks a written entry against the snapshot that
  produced it, and exits non-zero on any disagreement. Run it before committing
  an entry; after the commit nothing can be fixed. Takes the entry path.
- `fpl/summarise.py` — the readable digest of a committed entry, written to
  `reports/`. Derived output like `scores/`: regenerable, and rule 1 does not
  apply to it. Reads the entry alone, never a snapshot, so it still works from
  a clean checkout once `data/raw/` is long gone.
- `tests/fixtures.py` — synthetic snapshots and entries, shared by every suite.
  Build test data from here rather than hand-rolling snapshot JSON. Test
  modules import it as `from tests import fixtures`, which is what makes all
  three `unittest discover` spellings work.
- Everything else — not built. See `SPEC.md`.

New models import from `snapshot`, `features` and `log` — never from
`predict_baseline`. The baseline is meant to be beaten and deleted, so nothing
should depend on it.

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

`event/{id}/live/` is the scoring harness's source of actual points. `fetch.py`
writes it to `<snapshot>/live/<gw>.json` for gameweeks whose `data_checked` is
true, records which in `manifest.json` as `live_gameweeks`, and skips any file
already on disk. One request per gameweek, so scoring does not need a
six-minute per-player refetch. Read it back with `snapshot.load_live`, which
refuses a gameweek that has none.

### Snapshot timing

Take snapshots between gameweeks, not during one. A snapshot taken mid-gameweek
still contains a history row for every player, including those whose fixture has
not kicked off yet. That row reads 0 minutes and 0 points, and nothing
distinguishes it from a player who was left out — so a rolling-form feature
silently treats an unplayed match as a non-appearance. Bonus points are also
provisional until `data_checked` is true.

This is not a rule 2 violation, since the rounds involved are strictly before the
target. It is a completeness problem, and it biases predictions toward whichever
teams happened to have played before the snapshot was taken.

Worked example: `20260905T222906Z` was taken mid-GW3 with 8 of 10 fixtures
started and none finished. All 120 players across ARS, CHE, EVE and MUN carry a
zeroed GW3 row for a match that had not begun.

`fpl/predict_baseline.py` now excludes such rounds automatically and prints which
rounds it included and excluded on every run — read those two lines before
trusting the output. A snapshot taken between gameweeks is still preferable,
since an excluded round is data thrown away.

## Conventions

- Python 3.13, standard library plus `requests`. Add dependencies only when
  genuinely needed and pin them in `requirements.txt`.
- All timestamps UTC. FPL deadlines are published in UTC and timezone ambiguity
  near a deadline-sensitive system is unacceptable.
- Gameweek filenames zero-padded: `gw04`, not `gw4`.
- `scratch/` is gitignored space for exploratory runs. Never put a real
  prediction there, and never put an exploratory run in `predictions/`.
- Prefer explicit and readable over clever. This repo is read by humans
  evaluating the author.

## When something is ambiguous

Ask rather than guess. In particular, do not "fix" a failing script by removing
the failing part, and do not silently change model parameters — those are
decisions with consequences for the log.
