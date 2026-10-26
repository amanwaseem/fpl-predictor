"""The application: routes, problem-details errors, and HTTP caching.

    uvicorn fpl.api.app:app      # or: fpl-serve

The database comes from DATABASE_URL, opened at startup rather than import,
so importing the app (to export its OpenAPI, say) needs no database.
"""

import hashlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.openapi.utils import get_openapi

from fpl.api import problems
from fpl.api.routes import health, optimiser, v1
from fpl.db.session import make_engine

API_VERSION = "1"
# The data changes about once a week, when an entry is committed or scored.
# Five minutes keeps clients and CDNs from asking every time without leaving a
# fresh entry invisible for long; the ETag makes the re-check cheap.
CACHE_CONTROL = "public, max-age=300"
LIVE_PREFIXES = ("/v1/entries/",)


def etag_for(body):
    return f'"{hashlib.sha256(body).hexdigest()[:32]}"'


def matches(if_none_match, etag):
    """Whether an If-None-Match header covers `etag`, compared weakly (RFC 9110 13.1.2)."""
    tags = [tag.strip().removeprefix("W/") for tag in if_none_match.split(",")]
    return "*" in tags or etag in tags


async def http_caching(request: Request, call_next):
    """ETag and Cache-Control on successful /v1 reads; 304 when the client is current."""
    response = await call_next(request)
    # Advice is from a manager's live squad, which changes with every transfer
    # they make: not for a browser or CDN to keep.
    live = request.url.path.startswith(LIVE_PREFIXES)
    if request.method != "GET" or response.status_code != 200 \
            or not request.url.path.startswith("/v1/") or live:
        if request.url.path in ("/healthz", "/readyz") or live:
            response.headers["Cache-Control"] = "no-store"
        return response

    body = b"".join([chunk async for chunk in response.body_iterator])
    etag = etag_for(body)
    headers = {"ETag": etag, "Cache-Control": CACHE_CONTROL}
    if matches(request.headers.get("if-none-match", ""), etag):
        return Response(status_code=304, headers=headers)
    # raw_headers rather than a dict, which would keep one of each repeated header.
    buffered = Response(body, status_code=200)
    buffered.raw_headers = response.raw_headers
    buffered.headers.update(headers)
    return buffered


def openapi_with_problems(app):
    """The generated OpenAPI, with every error response as application/problem+json."""
    def build():
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version,
                                 description=app.description, routes=app.routes)
            for path in schema["paths"].values():
                for operation in path.values():
                    for status, response in operation.get("responses", {}).items():
                        content = response.get("content", {})
                        if int(status) >= 400 and "application/json" in content:
                            content[problems.MEDIA_TYPE] = content.pop("application/json")
            app.openapi_schema = schema
        return app.openapi_schema
    return build


def create_app(engine=None):
    """The app. Pass `engine` to use a database other than DATABASE_URL's."""

    @asynccontextmanager
    async def lifespan(app):
        app.state.engine = engine if engine is not None else make_engine()
        yield
        if engine is None:
            app.state.engine.dispose()

    app = FastAPI(
        title="fpl-predictor",
        version=API_VERSION,
        description="Committed FPL predictions, their scores, and the data behind them, "
                    "and a squad optimiser over them. The log is read-only. Errors are "
                    "RFC 9457 problem details.",
        lifespan=lifespan,
        redoc_url=None,
    )
    app.include_router(v1, prefix="/v1")
    app.include_router(optimiser, prefix="/v1")
    app.include_router(health)
    problems.install(app)
    app.middleware("http")(http_caching)
    app.openapi = openapi_with_problems(app)
    return app


app = create_app()
