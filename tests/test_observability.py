"""Logs, request ids and error reporting, fpl.api.observability (#86).

No database: /healthz and a route that raises are all these need. The Sentry
test sends the induced error through Sentry's own SDK to a transport that
keeps it, so it runs offline.
"""

import io
import json
import logging
import unittest
from contextlib import redirect_stdout

import sentry_sdk
from fastapi.testclient import TestClient
from sentry_sdk.transport import Transport

from fpl.api import observability
from fpl.api.app import create_app


class Kept(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)


def app_with_a_failing_route():
    app = create_app(engine=object())

    def broken():
        raise RuntimeError("induced, to test the error path")

    app.add_api_route("/v1/broken", broken)
    return app


class TestLogs(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        handler = logging.StreamHandler(self.stream)
        handler.setFormatter(observability.JSONFormatter())
        observability.log.addHandler(handler)
        observability.log.setLevel(logging.INFO)
        self.addCleanup(observability.log.removeHandler, handler)

    def lines(self):
        return [json.loads(line) for line in self.stream.getvalue().splitlines()]

    def test_each_request_is_one_json_line_with_its_id(self):
        with TestClient(create_app(engine=object())) as client:
            response = client.get("/healthz", headers={"X-Request-ID": "abc123"})
        self.assertEqual(response.headers["x-request-id"], "abc123")
        line = self.lines()[-1]
        self.assertEqual(line["message"], "request")
        self.assertEqual(
            (line["request_id"], line["status"], line["path"]), ("abc123", 200, "/healthz")
        )
        self.assertIn("duration_ms", line)

    def test_a_request_without_an_id_gets_one(self):
        with TestClient(create_app(engine=object())) as client:
            response = client.get("/healthz")
        self.assertEqual(len(response.headers["x-request-id"]), 32)

    def test_a_failed_request_is_a_500_with_its_id_in_the_answer_and_the_log(self):
        with TestClient(app_with_a_failing_route(), raise_server_exceptions=False) as client:
            response = client.get("/v1/broken", headers={"X-Request-ID": "err-1"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers["x-request-id"], "err-1")
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        lines = self.lines()
        self.assertEqual(lines[-1]["status"], 500)
        self.assertTrue(
            any(
                line["message"] == "unhandled error"
                and "exception" in line
                and line["request_id"] == "err-1"
                for line in lines
            )
        )

    def test_times_are_utc_with_milliseconds(self):
        with TestClient(create_app(engine=object())) as client:
            client.get("/healthz")
        self.assertRegex(self.lines()[-1]["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")


class TestRouteTemplate(unittest.TestCase):
    def test_the_router_prefix_is_put_back(self):
        self.assertEqual(
            observability.route_template("/v1/players/411", "/players/{player_id}"),
            "/v1/players/{player_id}",
        )
        self.assertEqual(observability.route_template("/healthz", "/healthz"), "/healthz")
        self.assertIsNone(observability.route_template("/nowhere", None))


class TestSentry(unittest.TestCase):
    def tearDown(self):
        sentry_sdk.init()  # off again: no DSN

    def test_off_without_a_dsn(self):
        self.assertFalse(observability.configure_sentry({}))

    def test_an_induced_error_reaches_sentry(self):
        transport = Kept()
        sentry_sdk.init(
            dsn="https://key@example.invalid/1",
            transport=transport,
            data_collection={"user_info": False, "http_bodies": []},
        )
        with (
            TestClient(app_with_a_failing_route(), raise_server_exceptions=False) as client,
            redirect_stdout(io.StringIO()),
        ):
            response = client.get("/v1/broken", headers={"X-Request-ID": "boom-1"})
            self.assertEqual(response.status_code, 500)
        sentry_sdk.flush()
        events = [
            item.payload.json
            for env in transport.envelopes
            for item in env.items
            if item.headers.get("type") == "event"
        ]
        self.assertTrue(events, "no event reached the transport")
        self.assertEqual(len(events), 1, "reported once, not twice")
        values = events[0]["exception"]["values"]
        self.assertEqual(values[-1]["type"], "RuntimeError")
        self.assertEqual(events[0]["tags"]["request_id"], "boom-1")


class TestTelemetry(unittest.TestCase):
    def test_off_without_an_endpoint(self):
        self.assertFalse(observability.configure_telemetry(create_app(engine=object()), environ={}))


if __name__ == "__main__":
    unittest.main()
