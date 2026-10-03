# 0007. The baseline is the ship gate, and must itself beat naive predictors

- **Status:** Accepted
- **Date:** 2026-09-07
- **Issues:** #1, #3, #8

## Context

"Our model is accurate" means nothing without a reference point. And a
sophisticated model can easily be worse than something simple — FPL points are
noisy, and most of the signal is who plays.

The pipeline after prediction (scoring, optimiser, API, frontend) also needed
something to run on before any serious model existed.

## Decision

- `baseline-v1` (`fpl/predict_baseline.py`) is deliberately simple: recent
  minutes and points per 90, shrunk toward a positional prior, scaled by
  availability and fixture count. No machine learning.
- **Any model that does not beat the baseline does not ship**, judged on
  committed, out-of-sample entries (0006).
- **Logging is not shipping.** A model is logged once held-out backtests
  suggest it might win, because the log is the only way to find out. It ships —
  becomes what the optimiser and the public scorecard recommend — only after
  its entries have beaten the baseline's on the same gameweeks.
- **The baseline has a gate of its own.** Every score compares it with naive
  predictors on the same rows, read from the prediction snapshot: all zeros,
  and FPL's own `form` and `points_per_game` (`fpl/score.py`). Beating the
  baseline means little until the baseline is shown to beat these. At GW4 it
  did: MAE 1.153, against 1.277 (`form`), 1.365 (`points_per_game`) and 1.436
  (all zeros).
- The baseline is meant to be beaten and deleted. New models import from
  `fpl.snapshot`, `fpl.features` and `fpl.log`, never from it — a test enforces
  that for `form-fixture-v1`.

## Consequences

- Every model claim has a stated, reproducible reference.
- The baseline must keep being logged for as long as anything is compared with
  it, even once it is clearly beaten.
- "Beats" needs a definition per decision — pooled MAE, Spearman, legal-XI
  points captured — and over a handful of gameweeks the answer can be noise.
  The gate is applied to cumulative pooled figures, which only mean much after
  about three gameweeks.

## Revisit if

A logged model beats the baseline convincingly over enough gameweeks: it then
becomes the reference, and the baseline can retire from the log.
