# 0017. A uv lockfile replaces the hand-pinned requirements files

- **Status:** Accepted
- **Date:** 2026-10-25
- **Issues:** #76, #75, #19

## Context

Since #19, every version the project installs, transitive ones included, was
pinned by hand in `requirements.txt`, and later in four more files: one each
for the database, the optimiser and the API, and one for development. Each log
entry's reproducibility claim rests on those pins. Five files, kept in step by
hand, with `-r` includes between them, had started to cost more than they
gave: a new dependency meant finding and pinning its transitive dependencies
in the right file, and nothing checked that the files agreed with
`pyproject.toml`.

## Decision

- **uv, with a committed `uv.lock`.** `pyproject.toml` states what is
  needed, in ranges; the lock records the exact version and hash of
  everything, resolved once for every platform. `uv sync --locked` installs
  the lock as it is, and fails if it no longer matches `pyproject.toml`, so
  a dependency added without `uv lock` stops CI.
- **Dependency groups, by purpose.** `[project] dependencies` is what a
  prediction needs, `requests` alone, as before. `db`, `optimise`, `api` and
  `dev` are groups; `dev`, the default, includes the rest. The entry and
  deadline workflows install with `--no-dev`, and so does the RUNBOOK for an
  entry made by hand, so the runtime that makes a log entry is no bigger than
  it was. An `ml` group arrives with the learned
  model (#85), when it has something in it.
- **The versions did not move.** The first lock was pinned back to every
  version the requirements files held, so the switch changed the tooling and
  nothing it installs. Only the new tools (mypy, pre-commit) are new.
- **CI and the image install the lock.** One composite action,
  `.github/actions/uv`, sets up every workflow; it and the Dockerfile pin the
  same uv, 0.13.0. The image installs the package itself
  (`--no-editable`), as 0015 allows; a checkout installs it editable, as 0011
  requires.
- *Rejected:* `pip-tools`, compiling the requirements files from
  `pyproject.toml`. It would keep five files, and the lock would still be one
  per platform.

## Consequences

- One file to review when a version changes, with hashes, instead of five
  without.
- uv is needed to set up a checkout. `pip install -e .` still works for the
  package alone, without the lock's guarantees.
- `uv.lock` is long and generated. It is reviewed as a diff of versions, not
  read.

## Revisit if

- uv's lock format, or its handling of dependency groups, changes in a way
  that makes an old lock unreadable.
- Python packaging standardises a lock file (PEP 751) that tools other than uv
  can install from; then the lock could be in that format instead.
