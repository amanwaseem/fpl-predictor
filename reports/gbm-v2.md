# gbm-v2: held-out report

Rolling-origin cross-validation by season: each season predicted by a model trained on the seasons before it alone. 95% intervals from a bootstrap over gameweeks. "Naive" is recent points per 90 times recent minutes, on the same rows. Lower MAE is better; a gain above zero is gbm-v2 ahead. Bias is mean predicted minus actual points: below zero is under-predicting.

Not evidence of how gbm-v2 predicts: that is the log, from its first entry. This decides whether to log it (SPEC section 6).

| Held out | Rows | MAE | MAE 95% | Naive MAE | Gain | Gain 95% | Spearman | Spearman 95% | Bias |
|---|---|---|---|---|---|---|---|---|---|
| 2023/24 | 28742 | 0.957 | 0.928 to 0.988 | 1.025 | +0.068 | 0.056 to 0.078 | 0.697 | 0.687 to 0.705 | -0.006 |
| 2024/25 | 26919 | 0.995 | 0.963 to 1.028 | 1.060 | +0.065 | 0.053 to 0.076 | 0.712 | 0.702 to 0.720 | +0.005 |
| 2025/26 | 29338 | 0.965 | 0.936 to 0.999 | 1.060 | +0.096 | 0.078 to 0.110 | 0.724 | 0.711 to 0.733 | -0.066 |
| 2026/27 so far | 6562 | 1.169 | 1.092 to 1.290 | 1.271 | +0.102 | 0.048 to 0.152 | 0.698 | 0.646 to 0.731 | -0.028 |
