# 0008. Predict components and sum them; the interface waits for real components

- **Status:** Accepted
- **Date:** 2026-09-06
- **Issues:** #8, #32

## Context

FPL points are a sum of separately scored events: minutes played, goals,
assists, clean sheets, saves, defensive contributions, bonus, cards. A single
regressor on total points gives one number and no way to tell which part of it
is wrong.

## Decision

- Predict each scoring component and sum them: minutes (which gates the rest),
  goals and assists (conditioned on minutes), clean sheet (team-level, shared
  across a defence), saves, defensive contribution, and bonus.
- Order the work by where the error is. GW4's review put goals, assists and
  clean sheets at about 42% of points, with the largest misses whole clubs
  (−30 to +58). So team-level components came first (#31, assembled in
  `form-fixture-v1`, #32); minutes, whose estimate was already good (MAE 10.9;
  115 of 131 players given 80+ expected minutes played 60+), comes last.
- **The `ComponentModel` interface is deliberately not designed yet.** It is
  extracted from two concrete components once they exist, rather than guessed
  in advance and then bent to fit.
- Each entry keeps intermediate outputs (`expected_minutes`, `points_per_90`)
  so a bad week can be attributed to minutes or to rate even before the
  components are separate.

## Consequences

- Failures are legible: if defenders are mispredicted, the clean-sheet
  component is the first suspect, and the score breaks down by position and by
  club to point there.
- More moving parts, each needing its own held-out case (0006) before it stays.
- Until the interface exists, components live inside a model module rather than
  being swappable.

## Revisit if

The components, summed, cannot beat a single well-regularised regressor on
held-out backtests — legibility is worth some accuracy, but not unlimited
accuracy.
