"""Tests for the built site served by the API (#121, ADR 0025): no database, no Node."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from fpl.api import site
from fpl.api.app import create_app
from fpl.api.problems import MEDIA_TYPE

NOWHERE = "postgresql+psycopg://nobody@127.0.0.1:1/nothing?connect_timeout=1"
INDEX = "<!doctype html><title>fpl-predictor</title>"


def built_site():
    """A directory shaped like Vite's output."""
    root = Path(tempfile.mkdtemp())
    (root / "index.html").write_text(INDEX)
    (root / "assets").mkdir()
    (root / "assets" / "index-abc123.js").write_text("console.log(1)")
    (root / "favicon.svg").write_text("<svg/>")
    return root


class SiteCase(unittest.TestCase):
    web: Path | None = None

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(NOWHERE)
        cls.client = TestClient(create_app(cls.engine, web=cls.web), raise_server_exceptions=False)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.engine.dispose()


class TestWithASite(SiteCase):
    web = built_site()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.web, ignore_errors=True)

    def assertIndex(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, INDEX)
        self.assertEqual(response.headers["cache-control"], site.REVALIDATE)

    def assertProblem404(self, response):
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["content-type"], MEDIA_TYPE)

    def test_the_root_is_the_site(self):
        self.assertIndex(self.client.get("/"))

    def test_a_client_side_route_gets_the_site_so_deep_links_work(self):
        self.assertIndex(self.client.get("/gameweeks/9"))

    def test_hashed_assets_are_cached_for_good(self):
        response = self.client.get("/assets/index-abc123.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], site.IMMUTABLE)

    def test_a_missing_asset_is_a_404_not_the_page(self):
        self.assertProblem404(self.client.get("/assets/index-old.js"))

    def test_other_files_in_the_build_are_served(self):
        response = self.client.get("/favicon.svg")
        self.assertEqual(response.text, "<svg/>")

    def test_the_api_keeps_its_own_404s(self):
        for path in ("/v1/nothing-here", "/v1", "/healthz/x", "/docs/x"):
            with self.subTest(path=path):
                self.assertProblem404(self.client.get(path))

    def test_the_api_and_its_docs_still_answer(self):
        self.assertEqual(self.client.get("/healthz").json(), {"status": "ok"})
        self.assertIn("swagger", self.client.get("/docs").text.lower())

    def test_the_catch_all_is_not_in_the_openapi_document(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertFalse([p for p in paths if not p.startswith(("/v1", "/healthz", "/readyz"))])

    def test_nothing_outside_the_build_is_served(self):
        for path in ("/..%2F..%2Fetc%2Fpasswd", "/%2e%2e/%2e%2e/etc/passwd"):
            with self.subTest(path=path):
                self.assertNotIn("root:", self.client.get(path).text)


class TestWithoutASite(SiteCase):
    def test_the_root_is_a_404_as_before(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["content-type"], MEDIA_TYPE)


class TestTheDirectory(unittest.TestCase):
    def test_unset_means_no_site(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(site.directory())

    def test_a_directory_without_a_build_is_refused(self):
        empty = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"FPL_WEB_DIR": empty}):
            with self.assertRaises(SystemExit) as raised:
                site.directory()
        self.assertIn("npm run build", str(raised.exception))
        shutil.rmtree(empty)
