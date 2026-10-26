# 0019. A learned model, trained with LightGBM and evaluated without it

- **Status:** Accepted
- **Date:** 2026-10-26
- **Issues:** #85, #51, #84, #75

## Context

The logged models are hand-written rules over a few gameweeks of form.
2016/17 to 2025/26 are now training data (0018), with one row of
pre-deadline inputs per player per gameweek (#84). A gradient-boosted model
can learn from about 245,000 such rows what the rules guess at: how minutes,
form, price, fixture and last season combine.

Three constraints shape it:

- An entry runs with `requests` alone (0017), and is re-run and verified from
  the snapshot that made it. LightGBM is a compiled library with numpy, scipy
  and, on macOS, an OpenMP runtime.
- A model is fixed from its first entry (0005). Its trained weights have to be
  as reproducible as its code.
- Past seasons carry no injury flags, so a model trained on them cannot learn
  what "doubtful" means.

## Decision

- **gbm-v1 is four LightGBM models.** Minutes in two stages, as #51 asked:
  P(60+), then minutes given 60+ and given fewer, combined as
  `p * long + (1 - p) * short` and capped at 90 a fixture. Points by a
  Tweedie regression, whose mass at zero and long tail fit a gameweek's
  points.
- **Trained with LightGBM, evaluated without it.** Training is in the `ml`
  group. Each model is saved in LightGBM's text format, and
  `fpl.models.trees` walks its trees in plain Python by LightGBM's own rules
  for missing values and split direction. The tests check it against LightGBM
  to twelve decimal places, so an entry still needs `requests` alone.
- **Inputs come from the same builder in both places.** Training reads
  `player_gameweek_features`; a prediction builds the same row from the
  snapshot with the same pure function (`fpl.models.feature_table`). The
  backtest replays it through the leak test like every other model.
- **Availability is applied after the model**, as every logged model applies
  it and as `fpl-verify` checks: the model predicts as if the player is
  available, and the snapshot's chance of playing scales its points and
  minutes.
- **The artifact is a release asset with a committed registry.** The four
  models are one JSON file, published as the `model-gbm-v1` release asset;
  `models/registry.json` records its SHA-256, URL, training seasons, feature
  version, LightGBM version and the git SHA it was trained at. A prediction
  fetches it once into `data/models/` and refuses a file whose hash differs.
- **Judged held out, twice.** Rolling-origin cross-validation by season,
  with intervals from a bootstrap over gameweeks, says how it does on seasons
  it never saw (`reports/gbm-v1.md`). Then `fpl-backtest --held-out --model
  gbm-v1` against `baseline-v1` on this season, which training never read,
  is the gate (0007): it is logged only if it beats the baseline there.
- *Rejected:* committing the artifact to git. It is a few megabytes of
  generated numbers that would be rewritten by every retrain; the hash in the
  registry fixes it just as firmly.
- *Rejected:* ONNX or treelite. Both would bring a runtime back into the
  entry's environment.

## Consequences

- An entry for gbm-v1 needs the release reachable once per machine; after
  that the cached file is used, by hash.
- Retraining, on more seasons or with new inputs, is a new version (gbm-v2),
  with its own registry entry and its own first entry.
- Only numerical splits and three objectives are supported by the evaluator;
  a model that needs more is refused when it is read.

## Revisit if

- The evaluator becomes a bottleneck: it walks about 1,200 trees for each of
  about 700 players, a second or two per entry.
- FPL's injury flags become available for past seasons, so availability could
  be learned rather than applied.
