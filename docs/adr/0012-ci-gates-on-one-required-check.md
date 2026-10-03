# 0012. CI gates on one required check

- **Status:** Accepted
- **Date:** 2026-10-02
- **Issues:** #6, #26

## Context

CI runs lint, the test suite on Linux and macOS, and the rule 1 immutability
check. Branch protection names the checks a merge needs, by job name — and a
matrix job reports as one check per leg (`test (ubuntu-latest)`,
`test (macos-latest)`). Requiring jobs individually means the settings must
change whenever a job or a matrix leg does, and a required check that never
reports blocks every PR.

## Decision

- One job, `ci`, needs every other job and passes only if all of them
  succeeded. Branch protection on `main` requires `ci` alone.
- `ci` runs with `if: always()`. Without it, a failed dependency would make
  `ci` *skipped* — and GitHub counts a skipped required check as passing.
- What runs, and why each piece is scoped as it is:
  - **Tests on macOS as well as Linux**: every real entry is produced on macOS.
  - **One Python, 3.13**: pinned so an entry can be reproduced from the
    environment that made it; testing a second version argues against the pin.
  - **Lint (`ruff check`), not format**: a formatter would rewrite most of the
    repository for no change in meaning and bury `git blame`.
  - **Coverage reported, not gated**: a threshold invites tests written for
    the number.
  - **The installed commands run from outside the repository**, and must
    refuse there (0011).

## Consequences

- Jobs can be added, renamed or split without touching repository settings.
- A red `ci` does not say which job failed; the PR's job list does.
- Entry PRs carry no Python, so lint cannot fail them; on deadline day a red
  `ci` points at the tests or rule 1.

## Revisit if

GitHub's rulesets gain a way to require "all checks from this workflow", making
the aggregate job unnecessary. Or CI time grows enough that per-job required
checks (to merge on a subset) are worth the settings churn.
