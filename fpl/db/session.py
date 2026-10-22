"""Connecting to the database named by DATABASE_URL.

Every command that touches the database gets its URL through here, so each
refuses one that cannot work, naming what is wrong without printing it
(fpl/db/url.py, #94).
"""

import os

from sqlalchemy import Engine, create_engine

from fpl.db.url import describe_problems, url_problems

LOCAL_URL = "postgresql+psycopg://fpl:fpl@localhost:5432/fpl"


def normalise_url(url):
    """Name the psycopg driver in a plain Postgres URL.

    Hosts hand out postgres:// or postgresql://, and SQLAlchemy reads both as a
    request for psycopg2, which is not installed.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


def checked(url, name="DATABASE_URL"):
    """`url` with the psycopg driver named, or SystemExit saying why it cannot work."""
    found = url_problems(url)
    if found:
        raise SystemExit(describe_problems(found, name))
    return normalise_url(url)


def database_url(environ=None):
    environ = os.environ if environ is None else environ
    url = environ.get("DATABASE_URL")
    if not url:
        raise SystemExit(
            "DATABASE_URL is not set.\n"
            f"For the local database: make up, then export DATABASE_URL={LOCAL_URL}"
        )
    return checked(url)


def make_engine(url=None) -> Engine:
    return create_engine(checked(url, "The database URL") if url else database_url(),
                         pool_pre_ping=True)
