"""POST /v1/optimise and GET /v1/entries/{id}/advice, against Postgres (#83).

The entry is a copy of the committed GW8 baseline: six hundred-odd real
players, so the solver has real squads to find. The FPL API is replaced by a
dict of canned responses. Needs a database; see tests/database.py.
"""

import shutil
from datetime import UTC, datetime, timedelta
from unittest import mock

from sqlalchemy import func, insert, select

from fpl import optimise
from fpl.api import limits, solver, upstream
from fpl.db.models import Gameweek, OptimiseResult, Season, UpstreamResponse
from fpl.db.project import project, read_log
from fpl.log.contract import read_entry
from tests.database import ROOT, require_test_database
from tests.test_db_api import SEASON, APICase

ENTRY = ROOT / "predictions" / "gw08_baseline-v1.csv"


def setUpModule():
    require_test_database()


class OptimiseCase(APICase):

    def setUp(self):
        super().setUp()
        (self.tmp / "predictions").mkdir()
        shutil.copy(ENTRY, self.tmp / "predictions" / ENTRY.name)
        with self.engine.begin() as c:
            c.execute(insert(Season).values(season=SEASON))
            c.execute(insert(Gameweek), [
                {"season": SEASON, "gameweek": gw, "finished": gw < 8, "data_checked": gw < 8,
                 "deadline_utc": datetime(2026, 8, 14, 17, 30, tzinfo=UTC)
                 + timedelta(weeks=gw - 1)} for gw in range(1, 9)])
        project(self.engine, read_log())
        self.rows, _ = read_entry(ENTRY)
        self.best = optimise.best_squad(self.rows)
        self.squad_ids = [p.player_id for p in self.best.xi + self.best.bench]
        self.by_id = {r["player_id"]: r for r in self.rows}
        unlimited = mock.patch.object(limits, "solver_window", limits.SlidingWindow(None))
        unlimited.start()
        self.addCleanup(unlimited.stop)

    def post(self, body, status=200):
        response = self.client.post("/v1/optimise", json=body)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def owned(self, ids=None):
        return [{"player_id": i, "selling_price": self.by_id[i]["price"]}
                for i in (ids or self.squad_ids)]


class TestOptimise(OptimiseCase):

    def test_with_no_squad_it_is_the_best_squad_from_scratch(self):
        body = self.post({})
        self.assertEqual((body["gameweek"], body["model_version"]), (8, "baseline-v1"))
        self.assertIsNone(body["plan"])
        self.assertEqual(body["squad"]["expected_points"], self.best.expected_points)
        self.assertTrue(body["deadline_passed"])

    def test_the_best_squad_needs_no_transfers(self):
        body = self.post({"squad": self.owned(), "bank": 0.0, "free_transfers": 1})
        self.assertEqual(body["plan"]["transfers"], [])
        self.assertEqual(body["plan"]["xi_gain"], 0)

    def test_a_weak_player_is_transferred_out(self):
        weakest_mid = min((r for r in self.rows if r["position"] == "MID"
                           and r["player_id"] not in self.squad_ids
                           and r["team"] not in {self.by_id[i]["team"] for i in self.squad_ids}),
                          key=lambda r: (r["predicted_points"], r["price"]))
        best_mid = max((p for p in self.best.xi if p.position == "MID"),
                       key=lambda p: p.predicted_points)
        ids = [weakest_mid["player_id"] if i == best_mid.player_id else i
               for i in self.squad_ids]
        body = self.post({"squad": self.owned(ids), "bank": 5.0, "free_transfers": 1})
        outs = [t["out"]["player_id"] for t in body["plan"]["transfers"]]
        self.assertIn(weakest_mid["player_id"], outs)
        self.assertGreater(body["plan"]["xi_gain"], 0)

    def test_a_repeat_is_served_from_the_cache(self):
        self.assertFalse(self.post({})["cached"])
        again = self.post({})
        self.assertTrue(again["cached"])
        self.assertEqual(again["squad"]["expected_points"], self.best.expected_points)
        with self.engine.connect() as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(OptimiseResult)), 1)

    def test_the_projection_empties_the_cache(self):
        """Cached results cannot outlive the entry, or the deploy, that made them."""
        self.post({})
        project(self.engine, read_log())
        with self.engine.connect() as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(OptimiseResult)), 0)

    def test_an_entry_that_does_not_exist_is_404(self):
        self.post({"gameweek": 7}, 404)
        self.post({"model": "nope-v1"}, 404)

    def test_a_squad_that_cannot_be_planned_from_is_422_naming_why(self):
        ids = self.squad_ids[:14] + [next(r["player_id"] for r in self.rows
                                          if r["position"] == "GKP"
                                          and r["player_id"] not in self.squad_ids)]
        body = self.post({"squad": self.owned(ids)}, 422)
        self.assertIn("GKP in the squad", body["detail"])

    def test_the_same_player_twice_is_422(self):
        squad = self.owned()
        squad[-1] = squad[0]
        self.post({"squad": squad}, 422)

    def test_a_horizon_beyond_one_gameweek_is_422_until_55(self):
        self.post({"horizon": 3}, 422)

    def test_running_out_of_time_is_503_with_retry_after(self):
        with mock.patch.object(solver, "TIME_LIMIT", 0.0):
            response = self.client.post("/v1/optimise", json={})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["retry-after"], "60")

    def test_over_the_rate_a_client_gets_429(self):
        with mock.patch.object(limits, "solver_window", limits.SlidingWindow((2, 60))):
            self.post({})
            self.post({})
            response = self.client.post("/v1/optimise", json={})
        self.assertEqual(response.status_code, 429)
        self.assertGreaterEqual(int(response.headers["retry-after"]), 1)


class TestAdvice(OptimiseCase):

    def setUp(self):
        super().setUp()
        picks = [{"element": i} for i in self.squad_ids]
        self.upstream = {
            "entry/42/": {"started_event": 1, "current_event": 7},
            "entry/42/event/7/picks/": {"active_chip": None, "picks": picks,
                                        "entry_history": {"bank": 15}},
            "entry/42/history/": {"current": [{"event": gw, "event_transfers": 0}
                                              for gw in range(1, 8)], "chips": []},
            "entry/42/transfers/": [],
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

    def test_a_team_id_gets_advice_on_its_squad(self):
        body = self.get("/v1/entries/42/advice")
        self.assertEqual((body["fpl_team_id"], body["gameweek"], body["picks_gameweek"]),
                         (42, 8, 7))
        self.assertEqual(body["plan"]["bank"], 1.5)
        # Unused every week since GW1, so banked to the most FPL allows.
        self.assertEqual(body["plan"]["free_transfers"], optimise.MAX_FREE_TRANSFERS)
        self.assertEqual(optimise.violations(optimise.Squad(**{
            **body["squad"],
            **{k: [optimise.Pick(**p) for p in body["squad"][k]] for k in ("xi", "bench")},
            "captain": optimise.Pick(**body["squad"]["captain"]),
            "vice_captain": optimise.Pick(**body["squad"]["vice_captain"])})), [])

    def test_free_transfers_can_be_given(self):
        body = self.get("/v1/entries/42/advice", free_transfers=0)
        self.assertEqual(body["plan"]["free_transfers"], 0)
        self.assertEqual(body["estimates"]["free_transfers"], "given")

    def test_upstream_responses_are_cached(self):
        self.get("/v1/entries/42/advice")
        calls = self.get_calls.call_count
        self.get("/v1/entries/42/advice", free_transfers=2)
        self.assertEqual(self.get_calls.call_count, calls)
        with self.engine.connect() as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(UpstreamResponse)), 4)

    def test_a_team_fpl_does_not_know_is_404(self):
        self.get("/v1/entries/43/advice", 404)

    def test_fpl_failing_is_502(self):
        self.get_calls.side_effect = upstream.requests.ConnectionError()
        self.get("/v1/entries/42/advice", 502)

    def test_a_team_that_started_after_the_gameweek_is_409(self):
        self.upstream["entry/42/"] = {"started_event": 8, "current_event": 8}
        self.get("/v1/entries/42/advice", 409)

    def test_a_free_hit_squad_is_not_the_one_planned_from(self):
        self.upstream["entry/42/event/7/picks/"] = {
            "active_chip": "freehit", "picks": [], "entry_history": {"bank": 0}}
        self.upstream["entry/42/event/6/picks/"] = {
            "active_chip": None, "picks": [{"element": i} for i in self.squad_ids],
            "entry_history": {"bank": 3}}
        body = self.get("/v1/entries/42/advice")
        self.assertEqual((body["picks_gameweek"], body["plan"]["bank"]), (6, 0.3))
