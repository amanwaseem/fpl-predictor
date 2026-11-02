"""The web frontend (#121), served by the API from the same origin (ADR 0025).

The site is built by Vite into a directory of static files: `index.html`, and
content-hashed files under `assets/`. FPL_WEB_DIR names that directory; the
Docker image sets it, and without it the API serves no site, as before, so
the tests and the CLIs never need Node.

The API's routing is left exactly as it is: its 404s, 405s and trailing-slash
redirects. The site only answers a GET or HEAD that no route matched, from
the 404 handler: a file at the top of the build, or else `index.html`, so
that a client-side route's deep link or reload works. Under /v1 a miss stays
the API's 404 problem, and under assets/ a stale page's old asset stays a 404.
"""

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from fpl.api import problems

# Vite names files under assets/ by their content, so they can be kept for good.
IMMUTABLE = "public, max-age=31536000, immutable"
# index.html names the current assets, so it is re-checked on every load.
REVALIDATE = "no-cache"


def directory():
    """The built site's directory from FPL_WEB_DIR, or None when it is unset."""
    value = os.environ.get("FPL_WEB_DIR")
    if not value:
        return None
    path = Path(value).resolve()
    if not (path / "index.html").is_file():
        raise SystemExit(f"FPL_WEB_DIR={value} has no index.html. Build it with: make web")
    return path


class Assets(StaticFiles):
    """The build's assets/, each cached for good."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = IMMUTABLE
        return response


def install(app: FastAPI, root: Path):
    """Serve the site in `root`: its assets, and its pages from the 404 handler.

    Call after problems.install, whose HTTP error handler this one wraps.
    """
    # Resolved, so the containment check below compares like with like.
    root = root.resolve()
    index = root / "index.html"
    app.mount("/assets", Assets(directory=root / "assets", check_dir=False), name="assets")

    async def not_found(request: Request, exc: HTTPException):
        path = request.url.path
        if (
            exc.status_code != 404
            or request.method not in ("GET", "HEAD")
            or path == "/v1"
            or path.startswith(("/v1/", "/assets/"))
        ):
            return await problems.http_error(request, exc)
        try:
            file = (root / path.lstrip("/")).resolve()
            top_level = file.parent == root and file.is_file()
        except (OSError, ValueError):  # a NUL byte, say: not a file of the build
            top_level = False
        page = file if top_level else index
        return FileResponse(page, headers={"Cache-Control": REVALIDATE})

    app.add_exception_handler(HTTPException, not_found)  # type: ignore[arg-type]
