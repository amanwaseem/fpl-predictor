"""Logs, errors, traces and metrics for the API (#86, docs/operations.md).

    LOG_FORMAT=text                       # plain lines, for a terminal (default: json)
    SENTRY_DSN=https://...                # errors to Sentry; off without it
    OTEL_EXPORTER_OTLP_ENDPOINT=https://... and OTEL_EXPORTER_OTLP_HEADERS=...
                                          # traces and metrics over OTLP; off without it

- **Logs** are one JSON object a line on stdout, which Fly collects: every
  request is one line (method, route, status, duration, request id), and every
  other line during a request carries its request id. The id is the caller's
  X-Request-ID, else Fly's, else a new one, and is returned in X-Request-ID,
  so a user's report can be matched to the log.
- **Errors** go to Sentry, with no request bodies or personal data: an FPL
  team id is a public number, and nothing else identifies anyone.
- **Traces and metrics** go to whatever OTLP endpoint is set. Request latency
  comes from the FastAPI instrumentation; the solver's time and the data's
  freshness are recorded here.

Nothing here polls: the machine stops when idle and the database suspends,
and a metric that woke either every minute would cost more than it told.
Freshness is the age of the ingest's latest check, as the last request for
the current season's /v1/status read it.
"""

import contextvars
import json
import logging
import os
import sys
import time
import uuid
from datetime import UTC, datetime

import sentry_sdk
from fastapi import Request

from fpl.api import problems

request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
log = logging.getLogger("fpl.api")

# When the ingest last checked, as /v1/status last read it, for the gauge.
_freshness: dict[str, datetime | None] = {"checked": None}
_providers = {"set": False}
_solver_seconds = None


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


def configure_sentry(environ=os.environ):
    """Errors to Sentry if SENTRY_DSN is set. Returns whether it is on."""
    dsn = environ.get("SENTRY_DSN")
    if not dsn:
        return False
    if sentry_sdk.get_client().is_active():
        return True
    sentry_sdk.init(
        dsn=dsn,
        environment=environ.get("FPL_ENVIRONMENT", "production"),
        release=environ.get("FLY_IMAGE_REF"),
        # No user info and no request bodies: an FPL team id is public, and
        # nothing else here identifies anyone.
        data_collection={"user_info": False, "http_bodies": []},
        # Traces are OpenTelemetry's; Sentry is for errors.
        traces_sample_rate=0.0,
    )
    return True


def configure_telemetry(app, environ=os.environ):
    """Traces and metrics over OTLP if an endpoint is set. Returns whether it is on.

    The providers are the process's, set once however many apps are made (the
    module's own `app`, then any made for a tool); each app is instrumented.
    """
    if not environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    if not _providers["set"]:
        _set_providers(environ)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz")
    return True


def _set_providers(environ):
    global _solver_seconds
    # The stable HTTP metric names (http.server.request.duration, in seconds),
    # which docs/dashboard.json queries, rather than the older ones. The
    # instrumentation reads it from the process environment.
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")
    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": environ.get("OTEL_SERVICE_NAME", "fpl-api")})
    tracer = TracerProvider(resource=resource)
    tracer.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer)
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=60_000)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
    meter = metrics.get_meter("fpl.api")
    _solver_seconds = meter.create_histogram(
        "fpl.solver.duration", unit="s", description="Time for one optimiser request's solves"
    )
    meter.create_observable_gauge(
        "fpl.data.age",
        callbacks=[_observe_freshness],
        unit="s",
        description="Seconds since the ingest last checked FPL against the database",
    )
    _providers["set"] = True


def _observe_freshness(options):
    from opentelemetry.metrics import Observation

    checked = _freshness["checked"]
    if checked is None:
        return []
    return [Observation((datetime.now(UTC) - checked).total_seconds())]


def record_freshness(checked_at):
    """The ingest's latest check, as /v1/status read it; the gauge ages from it."""
    _freshness["checked"] = checked_at


def record_solver_time(seconds):
    if _solver_seconds is not None:
        _solver_seconds.record(seconds)


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
    sentry_sdk.get_isolation_scope().set_tag("request_id", request_id.get())
    start = time.perf_counter()
    status = 500
    try:
        try:
            response = await call_next(request)
        except Exception:
            # Answered here rather than by Starlette's last-resort handler,
            # which runs outside this middleware: so the 500 carries the
            # request id, and so does its log line. Sentry is told first.
            sentry_sdk.capture_exception()
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


def check_sentry(environ=os.environ):
    """Send one induced error to Sentry, and say its event id. For checking the
    setup end to end from the deployed app: fly ssh console -C fpl-check-sentry."""
    if not configure_sentry(environ):
        raise SystemExit("SENTRY_DSN is not set: nothing to test.")
    try:
        raise RuntimeError("Induced by fpl-check-sentry (#86)")
    except RuntimeError:
        event = sentry_sdk.capture_exception()
    sentry_sdk.flush(timeout=10)
    print(f"Sent event {event} to Sentry. It should appear in the project within a minute.")
    return 0


def cli(argv=None):
    """`fpl-check-sentry`, or `python -m fpl.api.observability`."""
    import argparse

    argparse.ArgumentParser(description="Send one induced error to Sentry.").parse_args(argv)
    return check_sentry()


if __name__ == "__main__":
    sys.exit(cli())
