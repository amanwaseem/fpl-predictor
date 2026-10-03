# 0013. Players who change club mid-season

- **Status:** Open
- **Date:** 2026-10-03
- **Issues:** #8, #11

## Context

`player_id` is stable for a season; a player's club is not. Moves between
Premier League clubs happen in the January window, and late in the summer one,
which closes after the first gameweeks. Nothing has decided how a player's
history should be read after a move, and each part of the code currently does
whatever fell out of its own design. This record states what that is, so the
question gets decided rather than drifting.

## What each part does today

- **Live predictors** (`baseline-v1`, `form-fixture-v1`) take the club from the
  snapshot's bootstrap, which is the club at the deadline. Fixture count and the
  target fixtures follow the new club. That is correct.
- **The form window and the last-season prior are per player**, so they carry
  across the move: points per 90 earned at the old club, and last season's rate
  (`fpl/features.py:player_prior`, read from `history_past`) wherever it was
  earned.
- **`form-fixture-v1` scales the attacking rate by the fixture relative to the
  current club's usual goals** (`fpl/predict_fixture.py`, `usual_goals`). A rate
  earned at a weak attacking club is read against a strong club's baseline, or
  the reverse — the factor is calibrated for a player who stayed.
- **Team ratings** (`fpl/teams.py`) are built from fixtures, club by club, and
  are unaffected.
- **The backtest** reads a player's club at each target from his last fixture
  row before it, and refuses to fall back to his current club
  (`fpl/backtest.py:_as_of_row`), so a later move does not leak backwards.
- **Scoring** groups by the `team` column written in the entry, so a player
  counts for the club he was predicted at.

## Open questions

1. Should form earned at the old club carry over in full, be discounted, or be
   reset toward the prior? Minutes especially — a new signing's role is
   unknown.
2. Should the attacking rate be re-expressed relative to the old club's
   strength before the new club's fixture scales it?
3. Does last season's prior still apply to a player who has changed club since?

The natural owner is #8: a minutes component would make question 1 concrete,
and an attack component could carry the old club's strength explicitly.

## Decision

None yet. Deliberately: with one or two moves a window, any rule is a guess
until there is evidence of what moved players actually do.

## Consequences

- Until decided, moved players are predicted as though they had always been at
  the new club — possibly well off for the first few weeks after a move.
- The error is visible: scoring breaks down by club, and a moved player's
  misses show up under his new club.

## Revisit if

The January window opens, or a moved player's misses show in the by-club
breakdown. Decide it then, judged held out on the players who moved (0006),
and record the decision as a new ADR that supersedes this one.
