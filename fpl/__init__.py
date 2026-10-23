"""FPL points prediction.

Grouped by stage, in the order an entry passes through them:

    fpl/ingest/       fetch and read snapshots
    fpl/models/       features, team strength, the models
    fpl/log/          the prediction log: write, verify, summarise
    fpl/evaluation/   score logged entries, backtest candidates, the legal XI

Each stage imports from the stages before it, with one exception: each
model's command writes its entry through fpl/log/contract.py, the log's
schema. That module imports nothing from fpl, and the subpackages' __init__
files import nothing, so the two directions cannot make an import cycle.
tests/test_layout.py holds the layout to that.

Beside the stages, and outside the order:

    fpl/db/           the Postgres database: schema, loading, the log's copy
    fpl/api/          the read API over the database
    fpl/optimise.py   the squad optimiser, on fpl/evaluation/xi.py's rules
    fpl/paths.py      where the commands may run

Each command is a module in this package with a console script, declared in
pyproject.toml. A prediction needs only `pip install -r requirements.txt`; the
database, the API and the optimiser have requirements files of their own:

    fpl-fetch              # snapshot the FPL API to data/raw/
    fpl-predict-baseline   # write a prediction log entry
    fpl-verify             # check an entry against its snapshot

`fpl-<name>` and `python -m fpl.<package>.<module>` run the same function. Both resolve
data/raw and predictions/ relative to the working directory, so both refuse
to run anywhere but the repository root (fpl/paths.py).
"""
