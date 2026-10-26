# gbm-v1: held-out report

Rolling-origin cross-validation by season: each season predicted by a model trained on the seasons before it alone. 95% intervals from a bootstrap over gameweeks. "Naive" is recent points per 90 times recent minutes, on the same rows. Lower MAE is better; a gain above zero is gbm-v1 ahead.

Not evidence of how gbm-v1 predicts: that is the log, from its first entry. This decides whether to log it (SPEC section 6).

| Held out | Rows | MAE | MAE 95% | Naive MAE | Gain | Gain 95% | Spearman | Spearman 95% |
|---|---|---|---|---|---|---|---|---|
| 2023/24 | 28742 | 0.957 | 0.928 to 0.988 | 1.025 | +0.068 | 0.056 to 0.078 | 0.697 | 0.687 to 0.705 |
| 2024/25 | 26919 | 0.995 | 0.963 to 1.028 | 1.060 | +0.065 | 0.053 to 0.076 | 0.712 | 0.702 to 0.720 |
| 2025/26 | 29338 | 0.965 | 0.936 to 0.999 | 1.060 | +0.096 | 0.078 to 0.110 | 0.724 | 0.711 to 0.733 |
| 2026/27 so far | 4551 | 1.202 | 1.116 to 1.342 | 1.315 | +0.113 | 0.050 to 0.185 | 0.681 | 0.612 to 0.723 |
