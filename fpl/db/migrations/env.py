"""Alembic's entry point: which database, and which schema to compare against.

The URL comes from `sqlalchemy.url` when a caller sets it (the tests do), and
from DATABASE_URL otherwise.
"""

import logging

from alembic import context

from fpl.db.models import Base
from fpl.db.session import database_url, make_engine
from fpl.db.url import DatabaseURLError

config = context.config
# On the `alembic` command line, its progress on stderr. Callers in code (the
# tests, fpl-migrate) build their own Config, which has no cmd_opts, and leave
# logging alone.
if config.cmd_opts is not None:
    logging.basicConfig(format="%(levelname)-5.5s [%(name)s] %(message)s", level=logging.WARNING)
    logging.getLogger("alembic").setLevel(logging.INFO)

target_metadata = Base.metadata


def url():
    try:
        return config.get_alembic_option("sqlalchemy.url") or database_url()
    except DatabaseURLError as e:
        # `alembic` on the command line: its message, not a traceback.
        raise SystemExit(str(e)) from None


def run_migrations_offline():
    """Print the SQL instead of running it: `alembic upgrade head --sql`.

    Needs no database, only the dialect, so it runs without DATABASE_URL too.
    """
    offline_url = config.get_alembic_option("sqlalchemy.url") or "postgresql+psycopg://"
    context.configure(
        url=offline_url, target_metadata=target_metadata, literal_binds=True, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = make_engine(url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
