"""Security headers on every response, fpl.api.security (#178). No database."""

import shutil
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from fpl.api import security
from fpl.api.app import create_app
from tests.api.test_api_site import built_site


class TestSecurityHeaders(unittest.TestCase):
    web: Path
    client: TestClient

    @classmethod
    def setUpClass(cls):
        cls.web = built_site()
        app = create_app(engine=object(), web=cls.web)

        def broken():
            raise RuntimeError("induced, to test the error path")

        app.add_api_route("/v1/broken", broken)
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        shutil.rmtree(cls.web, ignore_errors=True)

    def assertHeaders(self, response, policy=security.SITE_POLICY):
        self.assertEqual(response.headers["content-security-policy"], policy)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["referrer-policy"], "strict-origin-when-cross-origin")

    def test_the_site_its_assets_and_its_deep_links(self):
        for path in ("/", "/gameweeks/9", "/assets/index-abc123.js", "/favicon.svg"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertHeaders(response)

    def test_the_api_its_errors_and_a_500(self):
        for path, status in (("/healthz", 200), ("/v1/nothing", 404), ("/v1/broken", 500)):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, status)
                self.assertHeaders(response)

    def test_the_docs_get_a_policy_that_lets_swagger_load(self):
        response = self.client.get("/docs")
        self.assertEqual(response.status_code, 200)
        self.assertHeaders(response, security.DOCS_POLICY)
        self.assertIn(security.SWAGGER_CDN, response.text)

    def test_the_site_allows_the_player_photos_and_no_other_origin(self):
        self.assertIn(f"img-src 'self' data: {security.PLAYER_PHOTOS}", security.SITE_POLICY)
        self.assertNotIn("unsafe", security.SITE_POLICY)
        self.assertIn("frame-ancestors 'none'", security.SITE_POLICY)

    def test_hsts_only_when_hosted(self):
        self.assertNotIn("strict-transport-security", self.client.get("/healthz").headers)
        with mock.patch.dict("os.environ", {"FPL_HOSTED": "1"}):
            response = self.client.get("/healthz")
        self.assertEqual(response.headers["strict-transport-security"], security.HSTS)
