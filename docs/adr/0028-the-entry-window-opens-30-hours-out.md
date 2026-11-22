# 0028. The entry window opens 30 hours out, once the previous gameweek has settled

- **Status:** Accepted
- **Date:** 2026-11-22
- **Issues:** #188, #68

## Context

The entry workflow (#68) is scheduled hourly and acted once the next deadline
was 12 hours away. The 12 hours were chosen as late enough for the final
press conferences and early enough for a dozen retries before the deadline.

GitHub doesn't keep that schedule. It starts scheduled runs late, or drops
them, under load. From 2026-10-23 to 2026-11-22, `entry.yml` ran 119 times on
its schedule: a median gap of 6.0 hours, 8.4 at the 90th percentile, and 10.8
at most. The watchdog's gaps were similar (median 5.3 hours, longest 26.7).
A 12-hour window could hold a single run.

GW11 did. The last run before the window was 14.8 hours out. The first run
inside it was 9.5 hours out, and the watchdog had fired four minutes earlier.
The entry worked, but if that run had failed, the next might have come after
the deadline. A missed deadline stays missed (ADR 0004).

## Decision

The window has two stages (`tools/entry_bot.py`):

- **Early, 30 hours out or less, once the previous gameweek has settled**
  (`data_checked`, bonus confirmed). Several runs fall inside it, so a failed
  one is retried. Waiting for the previous gameweek keeps the snapshot free of
  in-flight fixtures and provisional bonus (runbook section 1). `finished`
  alone is not enough: it flips hours earlier, and `usable_rounds` leaves a
  round out of the features until `data_checked`, so an entry made in that gap
  would miss the latest round while there was still time to wait for it.
- **Late, 12 hours out or less, whatever the previous gameweek's state.** This
  is the old window, kept as a fallback for tight turnarounds such as
  Christmas (GW17 on 26 December, GW18's deadline on the 29th). Its unsettled
  round is excluded, as before.

A previous gameweek missing from bootstrap (GW1's is last season's) counts as
settled.

The watchdog's alarm (`tools/check_deadline.py`) moves from 10 hours to 20.
That's after about two entry runs, with 20 hours left for a person. It is late
for the same reason the entry is, so an alarm at 10 hours might never run in
time. While the previous gameweek hasn't settled, the alarm stays at 10 hours:
the entry workflow is waiting for its late window on purpose then, and an
alarm at 20 would send someone to make the entry by hand for nothing (and
their `predict/gwNN` branch would then stop the workflow). The previous gameweek can
also settle late, inside 20 hours, with no entry run since. So until 10 hours
out, a watchdog alarm with no PR says to check the entry workflow's runs, not
to make the entry by hand.

## Consequences

- **The entry usually comes from a snapshot 24–30 hours before the deadline,
  not 9–12.** It misses the last day's injury news: Friday press conferences
  before a Saturday deadline, Thursday ones before a Friday deadline.
  `chance_of_playing_next_round` is read at snapshot time, and one entry per
  gameweek per model (ADR 0005) means it can't be refreshed. Some players'
  minutes will be further off. Missing the gameweek loses every prediction,
  which is worse.
- Each run's summary page names the stage that acted. The PR shows the
  snapshot id beside the deadline, so the snapshot's age is visible on every
  entry.
- A hand-made `predict/gwNN` branch with no PR now fails the workflow from 30
  hours out, not 12, once the previous gameweek has settled.

Considered and not chosen:

- **More `cron:` lines.** GitHub's throttling isn't documented, so more lines
  might not mean more runs.
- **A scheduler outside GitHub** (a Fly scheduled machine calling
  `workflow_dispatch`). It needs a GitHub token stored on Fly, and it is one
  more service to look after (ADR 0021).
- **The watchdog dispatching the entry.** That needs `actions: write` on a
  workflow meant to be read-only, and it runs on the same unreliable schedule.

## Revisit if

The entry's snapshot age shows up in the scores: moved minutes in the
`minutes` block of `scores/`, or misses that a later snapshot's
`chance_of_playing_next_round` would have caught. Revisit too if GitHub's
scheduling becomes reliable again.
