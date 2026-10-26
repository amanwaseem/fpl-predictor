# 0020. One baseline, one main model, at most one challenger

- **Status:** Accepted
- **Date:** 2026-10-26
- **Issues:** #109, #85, #75

## Context

By GW9 three models are logged: `baseline-v1`, `form-fixture-v1` and
`gbm-v1`. Nothing said when a model stops being logged, so each new one
would add a column for good, including models already shown to be weaker.

0005 fixes a model from its first entry, so that each `model_version` is one
clean record. That rule stands. What it left open is the other end of a
record: how one ends.

## Decision

- **Three roles, declared in `fpl/log/roles.py`.**
  - **The baseline**, `baseline-v1`: the yardstick. Logged every gameweek,
    never changed and never retired (0007). Without a fixed reference,
    "better" has no meaning over a season.
  - **The main model**: the best so far. It improves by new versions
    (`gbm-v1`, then `gbm-v2`), each with its own record (0005).
  - **At most one challenger**, being proven against the main model.
- **A challenger that beats the main model takes its place**, judged on
  committed entries for the same gameweeks, pooled, over at least three
  scored gameweeks (0007). The old main model then retires. A challenger
  that does not, retires itself.
- **Retiring a model ends its record, and keeps it.** It is no longer logged;
  its entries stay in the log, and its last gameweek is recorded with the
  reason. The weeks after it are not missed: the scoring harness and
  `/v1/models` say it is retired, and from when.
- **The rules are tests.** Exactly one baseline, at most one main model and
  one challenger, every logged model with exactly one role, and none both
  logged and retired. `fpl-entry` refuses to run a retired model.
- **Now:** `baseline-v1` the baseline, `form-fixture-v1` the main model,
  `gbm-v1` the challenger from GW9. `form-fixture-v1` retires once `gbm-v1`
  has three scored gameweeks and beats it.

## Consequences

- The log stays at two or three models a week, each with a reason to be
  there.
- A retired model's record is a closed book: comparable on the gameweeks it
  covered, and never mistaken for one with gaps.
- Promoting a challenger is a reviewed change to one file, in the same PR
  that changes `LOGGED_MODELS`.

## Revisit if

- Two candidates are worth proving at once. The limit of one challenger is
  there so the log stays readable and each comparison is head to head; it
  can be raised for a season with a reason.
- The log holds more than one season (#72): retirements are then per season.
