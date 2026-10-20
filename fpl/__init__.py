"""FPL points prediction.

Grouped by stage, in the order an entry passes through them:

    fpl/ingest/       fetch and read snapshots
    fpl/models/       features, team strength, the models
    fpl/log/          the prediction log: write, verify, summarise
    fpl/evaluation/   score logged entries, backtest candidates

Each command is a module in this package with a console script, declared in
pyproject.toml and installed by `pip install -r requirements.txt`:

    fpl-fetch              # snapshot the FPL API to data/raw/
    fpl-predict-baseline   # write a prediction log entry
    fpl-verify             # check an entry against its snapshot

`fpl-<name>` and `python -m fpl.<package>.<module>` run the same function. Both resolve
data/raw and predictions/ relative to the working directory, so both refuse
to run anywhere but the repository root (fpl/paths.py).
"""
