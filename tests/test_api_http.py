"""Tests for the API's HTTP behaviour that needs no data: errors, health, OpenAPI.

The app is given an engine that points at nothing, so these run without a
database. The routes' contracts against real data are in tests/test_db_api.py.
"""

import json
import re
import sys
import unittest
from decimal import Decimal
from pathlib import Path

from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from fpl.api.app import create_app, matches
from fpl.api.problems import MEDIA_TYPE
from fpl.api.queries import decode_cursor, encode_cursor

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import export_openapi  # noqa: E402

# Port 1 on localhost: refused at once, rather than waiting on a timeout.
NOWHERE = "postgresql+psycopg://nobody@127.0.0.1:1/nothing?connect_timeout=1"


class TestWithoutADatabase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(NOWHERE)
        cls.client = TestClient(create_app(cls.engine), raise_server_exceptions=False)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.engine.dispose()

    def assertProblem(self, response, status):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.headers["content-type"], MEDIA_TYPE)
        body = response.json()
        self.assertEqual(body["status"], status)
        self.assertEqual(body["instance"], response.request.url.path)
        return body

    def test_healthz_is_up_and_never_cached(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_readyz_says_the_database_is_unreachable(self):
        body = self.assertProblem(self.client.get("/readyz"), 503)
        self.assertIn("not reachable", body["detail"])

    def test_an_unknown_route_is_a_problem(self):
        self.assertProblem(self.client.get("/v1/nothing-here"), 404)

    def test_a_wrong_method_is_a_problem_naming_the_allowed_one(self):
        response = self.client.post("/v1/gameweeks")
        self.assertProblem(response, 405)
        self.assertEqual(response.headers["allow"], "GET")

    def test_an_invalid_parameter_is_a_problem_listing_what_was_wrong(self):
        body = self.assertProblem(self.client.get("/v1/gameweeks/39"), 422)
        self.assertEqual(body["errors"][0]["loc"], ["path", "gameweek"])

    def test_a_value_postgres_cannot_hold_never_reaches_it(self):
        self.assertProblem(self.client.get("/v1/models", params={"season": "2026/27\x00"}), 422)

    def test_a_database_failure_is_a_500_problem_not_a_traceback(self):
        body = self.assertProblem(self.client.get("/v1/models"), 500)
        self.assertNotIn("detail", body)


class TestConditionalRequests(unittest.TestCase):

    def test_if_none_match_is_a_list_of_tags(self):
        etag = '"abc"'
        for header, expected in [('"abc"', True), ('"x", W/"abc"', True), ("*", True),
                                 ('"xabcx"', False), ('"abcd"', False), ("", False)]:
            with self.subTest(header=header):
                self.assertEqual(matches(header, etag), expected)

    def test_repeated_headers_survive_the_caching_middleware(self):
        app = create_app(create_engine(NOWHERE))

        @app.get("/v1/test-headers")
        def two_cookies():
            response = Response("{}", media_type="application/json")
            response.set_cookie("a", "1")
            response.set_cookie("b", "2")
            return response

        with TestClient(app) as client:
            response = client.get("/v1/test-headers")
        self.assertEqual(len(response.headers.get_list("set-cookie")), 2)
        self.assertIn("etag", response.headers)


class TestCursor(unittest.TestCase):

    def test_every_cursor_issued_matches_the_pattern_it_comes_back_through(self):
        document = json.loads(export_openapi.document())
        parameters = document["paths"]["/v1/gameweeks/{gameweek}/predictions"]["get"]
        after = next(p for p in parameters["parameters"] if p["name"] == "after")
        pattern = next(s["pattern"] for s in after["schema"]["anyOf"] if "pattern" in s)
        for points in ("6.94", "6.9412345", "1E-7", "0E-7", "-1.5", "12345.5", "-0"):
            with self.subTest(points=points):
                cursor = encode_cursor(Decimal(points), 124)
                self.assertRegex(cursor, re.compile(pattern))
                self.assertEqual(decode_cursor(cursor), (Decimal(points), 124))


class TestOpenAPI(unittest.TestCase):

    def test_the_committed_document_matches_the_code(self):
        """A change to the API's contract has to arrive with its diff to docs/openapi.json."""
        committed = (ROOT / "docs" / "openapi.json").read_text()
        self.assertEqual(json.loads(committed), json.loads(export_openapi.document()),
                         "docs/openapi.json is stale. Regenerate it with: make openapi")

    def test_every_error_response_is_documented_as_a_problem(self):
        document = json.loads(export_openapi.document())
        for path, operations in document["paths"].items():
            for method, operation in operations.items():
                for status, response in operation["responses"].items():
                    if int(status) >= 400:
                        with self.subTest(path=path, method=method, status=status):
                            self.assertEqual(list(response["content"]), [MEDIA_TYPE])


if __name__ == "__main__":
    unittest.main()
