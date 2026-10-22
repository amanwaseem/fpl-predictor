"""Check that the database's copy of the log matches the committed files.

    fpl-reconcile

Compares the four log tables with predictions/ and scores/, row by row and
column by column, and exits non-zero on any difference: a row in git but not
in the database, one in the database but not in git, or one that differs. Run
`fpl-project` to rebuild the database's copy; the files are never changed.

A player score's `error` is computed by Postgres, so it is also checked
against the error column scores/ wrote, recomputed as the harness computed it:
in floats, then rounded. Rounding the exact Decimal would round some halves the
other way.

The tables are read in one REPEATABLE READ transaction, so a projection that
commits partway through cannot show up as differences.

Run from the repository root, with DATABASE_URL set.
"""

import argparse
import sys
from decimal import Decimal

from sqlalchemy import select

from fpl.db.models import PlayerScore, Prediction, PredictionEntry, ScoreEntry
from fpl.db.project import read_log
from fpl.db.session import exits_on_bad_url, make_engine
from fpl.paths import require_project_root

# Enough to see the pattern of a systematic difference without burying it.
MAX_REPORTED = 20


def table_rows(connection, model):
    return [dict(row._mapping) for row in connection.execute(select(model.__table__))]


def compare(table, in_git, in_database):
    """Every difference between two lists of rows for `table`, as readable lines."""
    keys = [column.name for column in table.primary_key]
    git = {tuple(row[k] for k in keys): row for row in in_git}
    database = {tuple(row[k] for k in keys): row for row in in_database}

    found = []
    for key in sorted(git.keys() - database.keys()):
        found.append(f"{table.name} {key}: in git, not in the database")
    for key in sorted(database.keys() - git.keys()):
        found.append(f"{table.name} {key}: in the database, not in git")
    for key in sorted(git.keys() & database.keys()):
        changed = [f"{column} is {git[key][column]!r} in git, "
                   f"{database[key].get(column)!r} in the database"
                   for column in git[key] if git[key][column] != database[key].get(column)]
        if changed:
            found.append(f"{table.name} {key}: " + "; ".join(changed))
    return found


def differences(engine, log):
    snapshot = engine.connect().execution_options(isolation_level="REPEATABLE READ")
    with snapshot as connection, connection.begin():
        player_scores = table_rows(connection, PlayerScore)
        for row in player_scores:
            harness_error = float(row["predicted_points"]) - row["actual_points"]
            row["error"] = Decimal(repr(round(harness_error, 2)))
        return (compare(PredictionEntry.__table__, log.entries,
                        table_rows(connection, PredictionEntry))
                + compare(Prediction.__table__, log.predictions,
                          table_rows(connection, Prediction))
                + compare(ScoreEntry.__table__, log.score_entries,
                          table_rows(connection, ScoreEntry))
                + compare(PlayerScore.__table__, log.player_scores, player_scores))


@exits_on_bad_url
def main():
    # The URL first: a bad one is refused before the log is read.
    engine = make_engine()
    try:
        log = read_log()
        found = differences(engine, log)
    finally:
        engine.dispose()
    if not found:
        print(f"The database matches the log: {log}.")
        return 0
    print(f"The database differs from the log in {len(found)} places:")
    for line in found[:MAX_REPORTED]:
        print(f"  {line}")
    if len(found) > MAX_REPORTED:
        print(f"  ... and {len(found) - MAX_REPORTED} more")
    print("Rebuild the database's copy with: fpl-project")
    return 1


def cli(argv=None):
    """`fpl-reconcile`, or `python -m fpl.db.reconcile`."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.parse_args(argv)
    require_project_root()
    return main()


if __name__ == "__main__":
    sys.exit(cli())
