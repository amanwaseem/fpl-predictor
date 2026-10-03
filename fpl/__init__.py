"""FPL points prediction.

Each command is a module in this package with a console script, declared in
pyproject.toml and installed by `pip install -r requirements.txt`:

    fpl-fetch              # snapshot the FPL API to data/raw/
    fpl-predict-baseline   # write a prediction log entry
    fpl-verify             # check an entry against its snapshot

`fpl-<name>` and `python -m fpl.<module>` run the same function. Both resolve
data/raw and predictions/ relative to the working directory, so both refuse
to run anywhere but the repository root (fpl/paths.py).
"""
