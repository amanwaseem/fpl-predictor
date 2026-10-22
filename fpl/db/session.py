"""Connecting to the database named by DATABASE_URL.

Every command that touches the database gets its URL through
database_url(), so each refuses one that cannot work, naming what is wrong
without printing it (fpl/db/url.py, #94). It is checked there once:
make_engine() uses a URL it is handed as given.
"""

import functools
import os

from sqlalchemy import Engine, create_engine

from fpl.db.url import DatabaseURLError, describe_problems, is_postgres, scheme_of, url_problems

LOCAL_URL = "postgresql+psycopg://fpl:fpl@localhost:5432/fpl"


def normalise_url(url):
    """Name the psycopg driver in a plain Postgres URL, whatever the scheme's case.

    Hosts hand out postgres:// or postgresql://, and SQLAlchemy reads both as a
    request for psycopg2, which is not installed. A scheme naming its driver is
    lowercased and otherwise left alone.
    """
    scheme = scheme_of(url)
    rest = url.partition("://")[2]
    if scheme in ("postgres", "postgresql"):
        return "postgresql+psycopg://" + rest
    if is_postgres(scheme):
        return f"{scheme}://{rest}"
    return url


def hosted(environ):
    """Whether this runs against the hosted database (FPL_HOSTED, set in fly.toml).

    There, nothing can fill in a missing user, password, host or database, so
    the URL must give them all.
    """
    return environ.get("FPL_HOSTED") == "1"


def database_url(environ=None):
    """DATABASE_URL with the psycopg driver named, or DatabaseURLError saying why not.

    The one place a URL is checked. Outside the hosted database, only what
    cannot connect is refused; libpq fills in the rest (fpl/db/url.py).
    """
    environ = os.environ if environ is None else environ
    url = environ.get("DATABASE_URL")
    if not url:
        raise DatabaseURLError(
            "DATABASE_URL is not set.\n"
            f"For the local database: make up, then export DATABASE_URL={LOCAL_URL}"
        )
    found = url_problems(url, complete=hosted(environ))
    if found:
        raise DatabaseURLError(describe_problems(found))
    return normalise_url(url)


def make_engine(url=None) -> Engine:
    """An engine for `url` as given, or for DATABASE_URL, checked, if none is."""
    return create_engine(normalise_url(url) if url else database_url(), pool_pre_ping=True)


def exits_on_bad_url(main):
    """Turn a DatabaseURLError into the command's exit, with its message.

    The library raises an ordinary exception, so tests and the API can handle
    it; only a command's entry point turns it into SystemExit.
    """
    @functools.wraps(main)
    def wrapper(*args, **kwargs):
        try:
            return main(*args, **kwargs)
        except DatabaseURLError as e:
            raise SystemExit(str(e)) from None
    return wrapper
