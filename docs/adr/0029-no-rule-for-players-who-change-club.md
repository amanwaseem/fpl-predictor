# 0029. No rule for players who change club mid-season, for now

- **Status:** Accepted
- **Date:** 2026-11-23
- **Issues:** #190, #8, #11
- **Supersedes:** [0013](0013-players-who-change-club-mid-season.md)

## Context

0013 recorded what each model does with a player who changes club (he is
predicted as if he had always been at the new one) and left the rule open
until there was evidence. The past-season archive (0018) is that evidence:
104 moves in the January window (a new club from GW17–26) over 2016/17–2025/26,
about ten a season.

The question was measured held out on gbm-v1, the challenger expected to
become the main model, with baseline-v1 as the yardstick. It was not measured
on form-fixture-v1: its rule (b), re-reading the attacking rate against the
old club's attack, would be a new version of a model that is stepping down,
and the backtest replays only the live season.

**Movers**: players whose club changed at GW17–26 and who had played for the
old club in the six gameweeks before. Their rows are the six gameweeks from
the move. **Players who stayed**: one club all season, in GW17–31, having
played in the six gameweeks before. gbm-v1 was refitted per held-out season
on the seasons before it (rolling origin, 2018/19–2025/26: 40 movers), and
again on every other season (2016/17–2025/26: 59 movers; this trains on later
seasons, which can teach league-wide trends but never the player's own rows).
The two agree. Intervals are 95%, from a bootstrap over players. The figures
below are the second run.

| | MAE | Bias (predicted − actual) | Minutes bias |
|---|---|---|---|
| gbm-v1, movers | 1.601 (1.356–1.851) | −0.277 (−0.515 to −0.016) | −9.8 |
| gbm-v1, stayed | 1.735 (1.707–1.763) | −0.010 | +0.2 |
| baseline-v1, movers | 1.627 (1.346–1.910) | −0.586 (−0.799 to −0.355) | −13.0 |
| baseline-v1, stayed | 1.791 (1.761–1.821) | −0.035 | +1.4 |

0013 feared form carried over from the old club would flatter a new signing.
It does the reverse: gbm-v1 predicts movers low, by about 15 minutes in each
of their first two gameweeks. Many moved because they had stopped playing, and the new
club starts them. Their error is no worse than that of players who stayed.

Each candidate rule touches movers only, so it changes nothing for players
who stayed:

| Rule, on gbm-v1 | Change in movers' MAE |
|---|---|
| (a) Minutes toward the position's mean, first 2 gameweeks, all the way | +0.142 (+0.064 to +0.220) |
| (a) The same, halfway | +0.034 (−0.009 to +0.074) |
| (a) Halfway, 4 or 6 gameweeks | +0.071, +0.106, both worse |
| (c) Leave out last season's figures | −0.021 (−0.038 to −0.007) |
| (d) Club spells from the snapshot | Nothing to change, below |

(a) removes the bias and adds error: which movers will start is not something
a positional mean knows. (d) is already right live: a gbm-v1 row takes its
club only for the gameweek predicted, which is the snapshot's club, and team
ratings credit each player's xG to the club he played for in that fixture
(`fpl/models/teams.py`, `team_matches`).

## Decision

- **No rule ships for the January 2027 window.** Movers are predicted as 0013
  described, by every logged model.
- **(c) goes into the next gbm version**, made for other reasons, not a version
  of its own. It is the one rule that helps, but by 0.02 points a gameweek on
  five or six players a window, which over a season is noise against any
  model's weekly error. A new logged version would start a new record (0005,
  0020) for that alone. To build it: in the live entry a mover is a player
  whose history has a fixture played for a club other than the snapshot's
  (`was_home` against the fixture's sides); in training, a player with a
  second spell in `player_clubs`. Score (c) again held out when it is built.
- **baseline-v1 and form-fixture-v1 are unchanged**, as frozen logged models.

## Consequences

- From GW20 a January signing who was out of favour will be predicted low for
  a few weeks, by about 15 minutes. He will show up in the by-club breakdown
  and among the misses, as 0013 expected.
- The spec's open question is closed.

## Revisit if

- The logged movers of 2026/27 miss by more than the archive's movers did:
  rows in the six gameweeks after a move, beyond the interval above.
- A new gbm version is built: it takes (c).
- Before summer 2027. Moves in the first gameweeks were not measured. They
  carry a whole season's prior from the old club, which is where (c) should
  matter most.
