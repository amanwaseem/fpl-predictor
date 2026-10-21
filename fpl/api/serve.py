"""Serve the API.

    fpl-serve                        # http://127.0.0.1:8000, docs at /docs
    fpl-serve --host 0.0.0.0 --port 8080

Reads DATABASE_URL. Needs no particular working directory: the API only reads
the database, never data/raw or predictions/.
"""

import argparse
import sys

import uvicorn


def cli(argv=None):
    """`fpl-serve`, or `python -m fpl.api.serve`."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true", help="restart on code changes")
    args = ap.parse_args(argv)
    uvicorn.run("fpl.api.app:app", host=args.host, port=args.port, reload=args.reload,
                proxy_headers=True)
    return 0


if __name__ == "__main__":
    sys.exit(cli())
