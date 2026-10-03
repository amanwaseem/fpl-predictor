# Architecture decision records

Why this project is built the way it is, one decision per record. The format
and the rules for changing a record are in [0001](0001-record-decisions-as-adrs.md).
SPEC.md says what the system does; these say why, and what would change the
answer.

| # | Decision | Status |
|---|---|---|
| [0001](0001-record-decisions-as-adrs.md) | Record decisions as ADRs | Accepted |
| [0002](0002-snapshot-first.md) | Snapshot first: never predict from the live API, and only from settled rounds | Accepted |
| [0003](0003-append-only-prediction-log.md) | The prediction log is append-only, and checked before it is written | Accepted |
| [0004](0004-a-missed-deadline-stays-missed.md) | A missed deadline stays missed | Accepted |
| [0005](0005-one-entry-per-gameweek-per-model.md) | One entry per gameweek per model, and a model is fixed from its first entry | Accepted |
| [0006](0006-only-the-log-is-evidence.md) | Only the log is evidence; held-out backtests decide what gets logged | Accepted |
| [0007](0007-the-baseline-is-the-ship-gate.md) | The baseline is the ship gate, and must itself beat naive predictors | Accepted |
| [0008](0008-predict-components-not-total-points.md) | Predict components and sum them; the interface waits for real components | Accepted |
| [0009](0009-model-fixture-difficulty-explicitly.md) | Model fixture difficulty explicitly, with our own xG ratings | Accepted |
