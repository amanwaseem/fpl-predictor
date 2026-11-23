"""GET /v1/live/{gw} against Postgres, with the FPL API replaced (#199)."""

from datetime import UTC, datetime, timedelta
from unittest import mock

from sqlalchemy import insert, update

from fpl.api import upstream
from fpl.db.models import Season, Team, UpstreamResponse
from tests.api.test_live import FIXTURES, LIVE, picks
from tests.db.test_db_api import SEASON, APICase
from tests.support.database import require_test_database


def setUpModule():
    require_test_database()


class TestLive(APICase):
    def setUp(self):
        super().setUp()
        with self.engine.begin() as c:
            c.execute(insert(Season).values(season=SEASON))
            c.execute(
                insert(Team),
                [
                    {
                        "season": SEASON,
                        "team_id": 15,
                        "code": 43,
                        "name": "Man City",
                        "short_name": "MCI",
                    },
                    {
                        "season": SEASON,
                        "team_id": 10,
                        "code": 54,
                        "name": "Fulham",
                        "short_name": "FUL",
                    },
                ],
            )
        self.upstream = {
            "event/11/live/": LIVE,
            "fixtures/?event=11": FIXTURES,
            "entry/42/event/11/picks/": picks(4, 5, 1),
        }
        patch = mock.patch.object(upstream.requests, "get", side_effect=self.fake_get)
        self.get_calls = patch.start()
        self.addCleanup(patch.stop)

    def fake_get(self, url, **kwargs):
        path = url.removeprefix(upstream.BASE + "/")
        response = mock.Mock()
        if path in self.upstream:
            response.status_code, response.json.return_value = 200, self.upstream[path]
        else:
            response.status_code = 404
        return response

    def test_scores_and_points(self):
        body = self.get("/v1/live/11")
        match = next(m for m in body["fixtures"] if m["fixture_id"] == 108)
        self.assertEqual((match["home"], match["away"], match["state"]), ("MCI", "FUL", "finished"))
        self.assertEqual(len(body["players"]), 8)
        self.assertIsNone(body["team"])

    def test_a_team_gets_its_picks_and_points(self):
        team = self.get("/v1/live/11", team=42)["team"]
        self.assertEqual((team["fpl_team_id"], team["points"]), (42, 3))

    def test_a_team_without_picks_that_gameweek_is_404(self):
        self.get("/v1/live/11", 404, team=43)

    def test_a_second_request_within_a_minute_is_served_from_the_cache(self):
        self.get("/v1/live/11")
        calls = self.get_calls.call_count
        self.get("/v1/live/11")
        self.assertEqual(self.get_calls.call_count, calls)

    def test_after_a_minute_fpl_is_asked_again(self):
        self.get("/v1/live/11")
        calls = self.get_calls.call_count
        with self.engine.begin() as c:
            c.execute(
                update(UpstreamResponse).values(
                    fetched_at_utc=datetime.now(UTC) - timedelta(seconds=61)
                )
            )
        self.get("/v1/live/11")
        # The live points and the fixtures, again.
        self.assertEqual(self.get_calls.call_count, calls + 2)

    def test_live_points_are_not_for_a_browser_or_cdn_to_keep(self):
        response = self.client.get("/v1/live/11")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_fpl_failing_is_502(self):
        self.get_calls.side_effect = upstream.requests.ConnectionError()
        self.get("/v1/live/11", 502)
