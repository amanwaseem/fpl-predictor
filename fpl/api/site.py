"""The web frontend (#121), served by the API from the same origin (ADR 0025).

The site is built by Vite into a directory of static files: `index.html`, and
content-hashed files under `assets/`. FPL_WEB_DIR names that directory; the
Docker image sets it, and without it the API serves no site, as before, so
the tests and the CLIs never need Node.

Every path the API owns keeps its own answer, a 404 problem included. Any
other GET is either a file in the build or one of the site's client-side
routes, which gets `index.html` so that a deep link or a reload works.
"""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

# Paths the API owns. A miss under these is the API's 404, never index.html.
API_PATHS = ("v1", "docs", "openapi.json", "healthz", "readyz")
# Vite names files under assets/ by their content, so they can be kept for good.
IMMUTABLE = "public, max-age=31536000, immutable"
# index.html names the current assets, so it is re-checked on every load.
REVALIDATE = "no-cache"


def directory():
    """The built site's directory from FPL_WEB_DIR, or None when there is none."""
    value = os.environ.get("FPL_WEB_DIR")
    if not value:
        return None
    path = Path(value).resolve()
    if not (path / "index.html").is_file():
        raise SystemExit(f"FPL_WEB_DIR={value} has no index.html. Build it with: npm run build")
    return path


def install(app: FastAPI, root: Path):
    """Serve the site in `root` for every GET the API's own routes do not take.

    Registered after them, so it only sees what they did not match.
    """
    # Resolved, so the containment check below compares like with like.
    root = root.resolve()
    index = root / "index.html"

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def site(path: str):
        if path.split("/", 1)[0] in API_PATHS:
            raise HTTPException(404, "Not Found")
        file = (root / path).resolve()
        if path and file.is_file() and file.is_relative_to(root):
            cache = IMMUTABLE if path.startswith("assets/") else REVALIDATE
            return FileResponse(file, headers={"Cache-Control": cache})
        if path.startswith("assets/"):
            # A stale page asking for an asset from an older build.
            raise HTTPException(404, "Not Found")
        return FileResponse(index, headers={"Cache-Control": REVALIDATE})
