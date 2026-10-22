"""Bring the database's schema up to the latest migration.

    fpl-migrate                 # against DATABASE_URL

`alembic upgrade head` without alembic.ini, which lives at the repository
root and is not installed: the migrations are found inside the package, so
this runs from the deployed image, which has no checkout. The deploy runs it
as Fly's release command, before any new machine takes traffic. If it fails,
the deploy stops and the running version keeps serving.

Needs no particular working directory: it reads only the database.
"""

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

import fpl.db.migrations
from fpl.db.session import database_url, make_engine

MIGRATIONS = Path(fpl.db.migrations.__file__).parent


def head():
    """The latest migration's revision id."""
    return ScriptDirectory(str(MIGRATIONS)).get_current_head()


def current(connection):
    """The revision the database is at, or None for an empty one."""
    return MigrationContext.configure(connection).get_current_revision()


def config(url):
    """An Alembic config pointing at the installed migrations, not alembic.ini."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def main():
    url = database_url()
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            before = current(connection)
        command.upgrade(config(url), "head")
        with engine.connect() as connection:
            after = current(connection)
    finally:
        engine.dispose()
    if before == after:
        print(f"The schema is already at {after}.")
    else:
        print(f"Migrated the schema from {before or 'empty'} to {after}.")
    return 0


def cli(argv=None):
    """`fpl-migrate`, or `python -m fpl.db.migrate`."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.parse_args(argv)
    return main()


if __name__ == "__main__":
    sys.exit(cli())
