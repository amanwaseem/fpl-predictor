"""Alembic's entry point: which database, and which schema to compare against.

The URL comes from `sqlalchemy.url` when a caller sets it (the tests do), and
from DATABASE_URL otherwise.
"""

from logging.config import fileConfig

from alembic import context

from fpl.db.models import Base
from fpl.db.session import database_url, make_engine

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def url():
    return config.get_main_option("sqlalchemy.url") or database_url()


def run_migrations_offline():
    """Print the SQL instead of running it: `alembic upgrade head --sql`.

    Needs no database, only the dialect, so it runs without DATABASE_URL too.
    """
    offline_url = config.get_main_option("sqlalchemy.url") or "postgresql+psycopg://"
    context.configure(url=offline_url, target_metadata=target_metadata,
                      literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = make_engine(url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
