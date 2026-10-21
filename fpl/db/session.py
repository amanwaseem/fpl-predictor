"""Connecting to the database named by DATABASE_URL."""

import os

from sqlalchemy import Engine, create_engine

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


def database_url(environ=None):
    environ = os.environ if environ is None else environ
    url = environ.get("DATABASE_URL")
    if not url:
        raise SystemExit(
            "DATABASE_URL is not set.\n"
            f"For the local database: make up, then export DATABASE_URL={LOCAL_URL}"
        )
    return normalise_url(url)


def make_engine(url=None) -> Engine:
    return create_engine(normalise_url(url) if url else database_url(), pool_pre_ping=True)
