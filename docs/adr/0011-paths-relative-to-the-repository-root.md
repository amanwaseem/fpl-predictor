# 0011. Paths are relative to the repository root, and the install is editable only

- **Status:** Accepted
- **Date:** 2026-10-03
- **Issues:** #5

## Context

Every path in the project — `data/raw/LATEST`, `predictions/`, `scores/`,
`scratch/` — resolves against the working directory. That was fine while
everything ran as `python -m fpl.x` from the repository root. Installed console
commands (`fpl-fetch`, …) run from anywhere: from the wrong directory
`fpl-fetch` would quietly start a second `data/raw/` with its own `LATEST`, and
a predictor would write a `predictions/` that is not the log. Neither is an
error by itself.

## Decision

- **Paths stay relative to the working directory**, so every file lands where
  it always has and the tests can run a season inside a temporary directory.
- **Commands refuse to run anywhere but the repository root.** The root is the
  directory whose `pyproject.toml` names `fpl-predictor` (`fpl/paths.py`).
  Every command that reads or writes those paths checks first and writes
  nothing if it refuses. `--help` works anywhere.
- *Rejected:* walking upward to find the root, as git does. A path the user
  types relative to a subdirectory and a default resolved against the root
  would be two anchors in one command.
- **The package is installed editable, and only editable**:
  `requirements.txt` ends in `-e .`. A regular install copies the code into
  site-packages, and after a `git pull` a deadline run would execute stale code
  that no commit describes. Editable, the commands always run the checkout.
- `pyproject.toml` declares the package; `requirements.txt` remains the lock of
  exact versions, because the reproducibility claim (0002) needs one and
  pyproject has none.

## Consequences

- A mistake made from the wrong directory fails loudly instead of creating a
  second state.
- After pulling a change to `pyproject.toml`, `pip install -r requirements.txt`
  has to be re-run once, or a new command will not exist. The runbook says so.
- Renaming the project means changing `fpl/paths.py` too; a test catches the
  drift.

## Revisit if

The project needs to run somewhere other than a checkout — a scheduled job, the
prediction API (#9) — at which point an explicit data directory (a flag or an
environment variable) is better than a working-directory convention.
