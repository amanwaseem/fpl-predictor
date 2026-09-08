"""FPL points prediction.

Entry points are modules in this package, run from the repository root:

    python -m fpl.fetch              # snapshot the FPL API to data/raw/
    python -m fpl.predict_baseline   # write a prediction log entry

Both resolve data/raw and predictions/ relative to the working directory, so
they must be run from the repository root.
"""
