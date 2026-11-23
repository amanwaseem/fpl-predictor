"""GET /v1/news against Postgres, with the FPL API replaced (#196)."""

from unittest import mock

from fpl.api import upstream
from tests.api.test_news import bootstrap, element
from tests.db.test_db_api import APICase
from tests.support.database import require_test_database


def setUpModule():
    require_test_database()


class TestNews(APICase):
    def setUp(self):
        super().setUp()
        self.bootstrap = bootstrap(element(1, "a"), element(2, "d", 75))
        patch = mock.patch.object(upstream.requests, "get", side_effect=self.fake_get)
        self.get_calls = patch.start()
        self.addCleanup(patch.stop)

    def fake_get(self, url, **kwargs):
        response = mock.Mock()
        response.status_code, response.json.return_value = 200, self.bootstrap
        return response

    def test_lists_the_doubts(self):
        body = self.get("/v1/news")
        self.assertEqual([i["web_name"] for i in body["items"]], ["P2"])
        self.assertEqual(body["items"][0]["chance_of_playing"], 75)

    def test_fpl_is_asked_once_for_many_readers(self):
        self.get("/v1/news")
        self.get("/v1/news")
        self.assertEqual(self.get_calls.call_count, 1)

    def test_fpl_failing_is_502(self):
        self.get_calls.side_effect = upstream.requests.ConnectionError()
        self.get("/v1/news", 502)
