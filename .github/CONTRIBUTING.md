# Contributing

Thanks for taking a look. Bug reports, questions and ideas are welcome as
[issues](https://github.com/amanwaseem/fpl-predictor/issues). For anything
bigger than a small fix, open an issue first, so we can agree on the approach
before you spend time on it.

## Getting set up

[`docs/development.md`](../docs/development.md) covers setup, every command, the
tests, the database, the API and the web frontend.

## The one rule that matters most

The prediction log in `predictions/` is append-only: a committed entry is
never changed, for any reason, and CI refuses a PR that changes one. Entries
are added by the scheduled entry workflow, before each deadline
([`docs/runbook.md`](../docs/runbook.md)). A change to how a logged model
predicts is a new model version, not an edit to the old one
([ADR 0005](../docs/adr/0005-one-entry-per-gameweek-per-model.md)).

## Making a change

1. Branch off `main`, one issue per branch.
2. Keep the checks green before you push:

   ```bash
   make lint                                # ruff, ruff format, mypy
   make test                                # the suite
   make test-db                             # the database tests (Docker)
   cd web && npm run lint && npm run format:check
   ```

   `uv run pre-commit install` runs most of these on every commit.
3. Open a pull request that says what changed, why, and how you checked it.
   CI's `ci` check must pass.
4. PRs are rebased onto `main`, never merged with a merge commit, so the
   history stays linear.

Commit messages start with a short summary line in the imperative ("Add",
"Fix", "Move"), and the body says why, not only what.

## Decisions

A choice that shapes the project (a new dependency, a new model, a change to
the log's rules) gets a short decision record in [`docs/adr/`](../docs/adr/README.md),
with what would make it worth revisiting.
