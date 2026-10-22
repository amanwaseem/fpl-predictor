"""Check the DATABASE_URL in the environment before a deploy, without printing it.

    DATABASE_URL=... python3 tools/check_database_url.py

The deploy workflow runs this on its `production` secret before Fly is
touched, so a malformed URL stops the deploy with the reason instead of a DNS
error from the release machine (#94). Standard library only, since it runs
before any dependency is installed. Exits 1 if the URL is missing or cannot
work; prints nothing from it either way.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fpl.db.url import POOLED, describe_problems, pooled, url_problems  # noqa: E402


def main(environ=None):
    environ = os.environ if environ is None else environ
    url = environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL is not set.")
        return 1
    # The deploy's secret is the hosted database's, and it runs the migrations
    # and the projection: complete, and direct rather than pooled.
    found = url_problems(url, complete=True) + ([POOLED] if pooled(url) else [])
    if found:
        print(describe_problems(found))
        return 1
    print("DATABASE_URL has the shape of a usable Postgres URL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
