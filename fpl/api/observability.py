"""Logs and request ids for the API (#86, docs/operations.md).

    LOG_FORMAT=text        # plain lines, for a terminal (default: json)

Logs are one JSON object a line on stdout, which Fly collects (`fly logs`):
every request is one line (method, route, status, duration, request id), and
every other line during a request carries its request id. The id is the
caller's X-Request-ID, else Fly's, else a new one, and is returned in
X-Request-ID, so a user's report can be matched to the log. An unhandled error
is logged with its traceback under the same id, and answered with a 500 that
carries it.
"""

import contextvars
import json
import logging
import os
import sys
import time
import uuid

from fastapi import Request

from fpl.api import problems

request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
log = logging.getLogger("fpl.api")


class JSONFormatter(logging.Formatter):
    """A log record as one JSON object, with the request id it belongs to."""

    def format(self, record):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
        entry = {
            "time": f"{stamp}.{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if request_id.get():
            entry["request_id"] = request_id.get()
        entry |= getattr(record, "fields", {})
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(environ=os.environ):
    """JSON lines on stdout, or plain text with LOG_FORMAT=text."""
    handler = logging.StreamHandler(sys.stdout)
    if environ.get("LOG_FORMAT", "json") == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    # The middleware logs each request once, with more in it than uvicorn's
    # access line; uvicorn's own errors still come through the root handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = name != "uvicorn.access"


def route_template(path, route_path):
    """The matched route's template with its router's prefix: '/v1/players/{player_id}'.

    A route inside an included router knows only its own part of the path, so
    the prefix is the leading segments of the request path it does not cover.
    """
    if route_path is None:
        return None
    covered = len(route_path.strip("/").split("/")) if route_path.strip("/") else 0
    segments = path.strip("/").split("/")
    prefix = segments[: max(len(segments) - covered, 0)]
    return "/" + "/".join(prefix + ([route_path.strip("/")] if covered else []))


async def request_logging(request: Request, call_next):
    """A request id for the request, and one log line when it is done."""
    rid = (
        request.headers.get("x-request-id")
        or request.headers.get("fly-request-id")
        or uuid.uuid4().hex
    )
    token = request_id.set(rid[:64])
    start = time.perf_counter()
    status = 500
    try:
        try:
            response = await call_next(request)
        except Exception:
            # Answered here rather than by Starlette's last-resort handler,
            # which runs outside this middleware: so the 500 carries the
            # request id, and so does its traceback in the log.
            log.exception("unhandled error")
            response = problems.problem_response(request, 500)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id.get()
        return response
    finally:
        route = request.scope.get("route")
        log.info(
            "request",
            extra={
                "fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "route": route_template(request.url.path, getattr(route, "path", None)),
                    "status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                }
            },
        )
        request_id.reset(token)
