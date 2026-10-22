# 0014. A static JSON API, not a hosted server

- **Status:** Superseded by 0015
- **Date:** 2026-10-21
- **Issues:** #9, #56, #72

## Context

#9 asks for the record over HTTP, so the scorecard and anyone else read it
from one place instead of parsing CSVs out of the repository: predictions per
gameweek with their provenance, filtering by position, team and price, and
later the optimiser's squad.

What it serves has three properties that decide how to serve it:

- **It changes about once a week.** One entry per deadline, one scoring run
  after each gameweek settles (ADR 0005).
- **It is the same for every viewer.** Nothing depends on who is asking.
- **It takes no input.** No one submits a squad, logs in, or asks a question
  the committed files do not already answer.

And one constraint: the log is the credibility mechanism (ADR 0003, 0006).
Whatever is served has to be traceable to what is committed. A server with its
own store is a second copy of the record that can drift from the first, and
it is one more thing that can be down on the day someone checks.

## Decision

- **Versioned static JSON files on GitHub Pages,** under `/api/v1/`:
  `gameweeks.json`, `gw/<n>/predictions.json` and `gw/<n>/results.json`. The
  schemas are in #9.
- **Built only from what is committed:** `predictions/`, `scores/` and git
  history, by `fpl-scorecard`, in a workflow on every push to `main`. No API
  call at build time, so one commit always builds the same files, apart from
  their `generated_at_utc`.
- **A copy of the record, never a second one.** Every number is copied from
  `scores/` or `predictions/`, unrounded and unrecomputed. Nothing on the site
  is typed by hand.
- **Versioned by path.** Fields may be added within v1. A breaking change is
  `/api/v2/`, with v1 left in place. Later seasons are added under
  `/api/v1/seasons/<season>/` (#72), which is additive.
- **Filtering happens in the browser,** over a gameweek's `predictions.json`.
- **There is no write path,** because there is nothing to write to. The
  read-only rule in #9 holds by construction rather than by care.

## Consequences

- No hosting cost, no uptime to promise, no secrets, nothing to patch. Pages
  serves the files from a CDN, and other sites can fetch them.
- The site can be rebuilt from any commit, so the repository's history is also
  the API's history.
- There are no per-user queries. Anything that needs input, such as
  "optimise my team", cannot be served this way.
- Filtering downloads a whole gameweek, a few hundred kilobytes, and runs in
  the browser.
- The site lags `main` by one workflow run, a few minutes.
- The next gameweek is not listed until its entry is committed: its deadline
  is only in the live API, which the build does not call.
- The API lives on GitHub. Moving it means moving a directory of files, not a
  service.

## Revisit if

- A feature needs user input, like "optimise my team" or a private squad. That
  needs a hosted service. It should read these files, or the committed ones,
  rather than keep a store of its own.
- Payloads grow past what a browser handles comfortably, for example several
  seasons or per-player history, and need pagination or a query service.
- The site approaches GitHub Pages' limits on size or bandwidth.
- The data starts changing more than weekly, for example live points during a
  gameweek.

Status moves to Accepted when #9's files are live.
