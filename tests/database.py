"""The Postgres the database tests run against: TEST_DATABASE_URL.

Those tests migrate it up and down and empty its tables, so a database whose
name does not end in _test is refused. Without one they skip, unless
FPL_REQUIRE_DB is set, as it is in CI's db job.

Locally: make test-db
"""

import os
import unittest
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url

from fpl.db.models import Base
from fpl.db.session import normalise_url

ROOT = Path(__file__).resolve().parent.parent
URL = os.environ.get("TEST_DATABASE_URL")


def require_test_database():
    """Call from setUpModule: skips the module when there is no database."""
    if not URL:
        if os.environ.get("FPL_REQUIRE_DB"):
            raise RuntimeError("FPL_REQUIRE_DB is set, but TEST_DATABASE_URL is not")
        raise unittest.SkipTest("TEST_DATABASE_URL is not set; make test-db runs these")
    name = make_url(normalise_url(URL)).database or ""
    if not name.endswith("_test"):
        raise RuntimeError(
            f"TEST_DATABASE_URL names {name!r}. The database tests empty every table in "
            "it, so they only run against a database whose name ends in _test."
        )


def alembic_config():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", normalise_url(URL).replace("%", "%%"))
    config.attributes["configure_logger"] = False
    return config


def empty_every_table(engine):
    names = ", ".join(Base.metadata.tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {names} CASCADE"))
