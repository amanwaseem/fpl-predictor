"""Contract tests for every /v1 route, against Postgres with a small season in it.

Needs a database; see tests/database.py. Locally: make test-db
"""

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import insert, update
from sqlalchemy.dialects.postgresql import Range

from fpl.api.app import create_app
from fpl.api.problems import MEDIA_TYPE
from fpl.db.models import (
    Fixture,
    Gameweek,
    Player,
    PlayerClub,
    PlayerFixtureStats,
    Season,
    SeasonPlayer,
    Team,
)
from fpl.db.project import project, read_log
from fpl.db.session import make_engine
from tests.database import ROOT, URL, alembic_config, empty_every_table, require_test_database
from tests.fixtures import TempCwd
from tests.log_files import write_entry, write_score

SEASON = "2026/27"


def setUpModule():
    require_test_database()


def stats(element_id, fixture_id, *, home, opponent, points):
    zeros = {column.name: 0 for column in PlayerFixtureStats.__table__.columns}
    return zeros | {"season": SEASON, "element_id": element_id, "fixture_id": fixture_id,
                    "snapshot_id": None, "was_home": home, "opponent_team": opponent,
                    "minutes": 90, "total_points": points, "goals_scored": 1,
                    "expected_goals": Decimal("0.61"), "expected_assists": None,
                    "influence": None, "creativity": None, "threat": None, "ict_index": None,
                    "starts": 1, "selected": None, "transfers_in": None, "transfers_out": None,
                    "transfers_balance": None, "value": 101}


def seed_season(engine):
    """GW1-8 of a season with GW1-7 settled; three players; one moved clubs at GW3."""
    with engine.begin() as c:
        c.execute(insert(Season).values(season=SEASON))
        c.execute(insert(Team), [
            {"season": SEASON, "team_id": 1, "code": 3, "name": "Arsenal", "short_name": "ARS"},
            {"season": SEASON, "team_id": 2, "code": 7, "name": "Aston Villa",
             "short_name": "AVL"}])
        c.execute(insert(Gameweek), [
            {"season": SEASON, "gameweek": gw,
             "deadline_utc": datetime(2026, 8, 14, 17, 30, tzinfo=UTC) + timedelta(weeks=gw - 1),
             "finished": gw <= 7, "data_checked": gw <= 7} for gw in range(1, 9)])
        c.execute(insert(Fixture), [
            {"season": SEASON, "fixture_id": 10, "gameweek": 1, "team_h": 1, "team_a": 2,
             "team_h_score": 2, "team_a_score": 1, "finished": True,
             "kickoff_utc": datetime(2026, 8, 15, 14, tzinfo=UTC)},
            {"season": SEASON, "fixture_id": 80, "gameweek": 8, "team_h": 2, "team_a": 1,
             "team_h_score": None, "team_a_score": None, "finished": False,
             "kickoff_utc": datetime(2026, 10, 24, 14, tzinfo=UTC)}])
        c.execute(insert(Player), [{"code": 1000 + i, "first_name": f"First{i}",
                                    "second_name": f"Second{i}"} for i in (1, 2, 3)])
        c.execute(insert(SeasonPlayer), [
            {"season": SEASON, "element_id": 1, "player_code": 1001, "web_name": "p1",
             "position": "MID"},
            {"season": SEASON, "element_id": 2, "player_code": 1002, "web_name": "p2",
             "position": "FWD"},
            {"season": SEASON, "element_id": 3, "player_code": 1003, "web_name": "p3",
             "position": "GKP"}])
        c.execute(insert(PlayerClub), [
            {"season": SEASON, "element_id": 1, "team_id": 2, "gameweeks": Range(1, 3)},
            {"season": SEASON, "element_id": 1, "team_id": 1, "gameweeks": Range(3, 39)}])
        c.execute(insert(PlayerFixtureStats), [stats(1, 10, home=False, opponent=1, points=8)])


class APICase(TempCwd):

    @classmethod
    def setUpClass(cls):
        command.upgrade(alembic_config(), "head")
        cls.engine = make_engine(URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        super().setUp()
        empty_every_table(self.engine)
        self.client = TestClient(create_app(self.engine))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        empty_every_table(self.engine)
        super().tearDown()

    def get(self, url, status=200, **params):
        response = self.client.get(url, params=params)
        self.assertEqual(response.status_code, status, response.text)
        if status >= 400:
            self.assertEqual(response.headers["content-type"], MEDIA_TYPE)
        return response.json()


class TestRoutes(APICase):
    """Entries for GW6 (scored) and GW8 (not yet) from two models; GW7 was missed."""

    def setUp(self):
        super().setUp()
        seed_season(self.engine)
        for model in ("baseline-v1", "form-fixture-v1"):
            write_entry(self.tmp, model, gameweek=8)
        write_entry(self.tmp, "baseline-v1", gameweek=6)
        write_score(self.tmp, "baseline-v1", gameweek=6, actual=(6, 2, 0))
        project(self.engine, read_log())

    def test_gameweeks_list_the_models_logged_for_each(self):
        body = self.get("/v1/gameweeks")
        self.assertEqual(body["season"], SEASON)
        self.assertEqual([g["gameweek"] for g in body["gameweeks"]], list(range(1, 9)))
        logged = {g["gameweek"]: g["models_logged"] for g in body["gameweeks"]}
        self.assertEqual(logged[6], ["baseline-v1"])
        self.assertEqual(logged[8], ["baseline-v1", "form-fixture-v1"])
        self.assertEqual(logged[7], [])

    def test_a_gameweek_has_its_fixtures_and_its_entries_scores(self):
        body = self.get("/v1/gameweeks/1")
        self.assertEqual(body["fixtures"][0] | {"kickoff_utc": None}, {
            "fixture_id": 10, "kickoff_utc": None, "home": "ARS", "away": "AVL",
            "home_score": 2, "away_score": 1, "finished": True})
        scored = self.get("/v1/gameweeks/6")["entries"]
        self.assertEqual(scored[0]["score"]["n"], 3)
        pending = self.get("/v1/gameweeks/8")["entries"]
        self.assertEqual([e["score"] for e in pending], [None, None])

    def test_a_gameweek_not_in_the_database_is_404(self):
        empty_every_table(self.engine)
        seed_season(self.engine)
        self.get("/v1/gameweeks/20", 404)

    def test_predictions_come_highest_first_with_actuals_once_scored(self):
        body = self.get("/v1/gameweeks/6/predictions", model="baseline-v1")
        points = [p["predicted_points"] for p in body["items"]]
        self.assertEqual(points, sorted(points, reverse=True))
        self.assertEqual(body["snapshot_id"], "20261023T080000Z")
        self.assertTrue(all(p["actual_points"] is not None for p in body["items"]))
        pending = self.get("/v1/gameweeks/8/predictions", model="baseline-v1")
        self.assertTrue(all(p["actual_points"] is None for p in pending["items"]))

    def test_predictions_filter_by_position_team_and_price(self):
        def ids(**params):
            body = self.get("/v1/gameweeks/8/predictions", model="baseline-v1", **params)
            return sorted(p["player_id"] for p in body["items"])
        self.assertEqual(ids(position="FWD"), [2])
        self.assertEqual(ids(team="ARS"), [1, 3])
        self.assertEqual(ids(max_price=5.0), [2, 3])

    def test_pages_join_up_into_the_whole_entry(self):
        whole = self.get("/v1/gameweeks/8/predictions", model="baseline-v1")["items"]
        pages, after = [], None
        while True:
            params = {"model": "baseline-v1", "limit": 1} | ({"after": after} if after else {})
            body = self.get("/v1/gameweeks/8/predictions", **params)
            pages += body["items"]
            after = body["next_cursor"]
            if after is None:
                break
        self.assertEqual(pages, whole)

    def test_any_cursor_the_schema_allows_is_a_valid_place_to_resume(self):
        """A cursor is a sort key, so every string matching its pattern pages correctly."""
        body = self.get("/v1/gameweeks/8/predictions", model="baseline-v1", after="999:1")
        whole = self.get("/v1/gameweeks/8/predictions", model="baseline-v1")
        self.assertEqual(body["items"], whole["items"])
        last = self.get("/v1/gameweeks/8/predictions", model="baseline-v1", after="-999:1")
        self.assertEqual(last["items"], [])
        self.get("/v1/gameweeks/8/predictions", 422, model="baseline-v1", after="not-a-cursor")

    def test_an_entry_that_does_not_exist_is_404(self):
        self.get("/v1/gameweeks/7/predictions", 404, model="baseline-v1")

    def test_a_player_has_clubs_history_and_predictions(self):
        body = self.get("/v1/players/1")
        self.assertEqual((body["first_name"], body["position"]), ("First1", "MID"))
        self.assertEqual(body["clubs"], [
            {"team": "AVL", "first_gameweek": 1, "last_gameweek": 2},
            {"team": "ARS", "first_gameweek": 3, "last_gameweek": 38}])
        self.assertEqual(body["history"][0]["opponent"], "ARS")
        self.assertEqual(body["history"][0]["expected_goals"], 0.61)
        self.assertEqual([(p["gameweek"], p["model_version"]) for p in body["predictions"]],
                         [(6, "baseline-v1"), (8, "baseline-v1"), (8, "form-fixture-v1")])
        self.assertIsNotNone(body["predictions"][0]["actual_points"])

    def test_an_open_ended_spell_has_no_last_gameweek(self):
        with self.engine.begin() as c:
            c.execute(update(PlayerClub).where(PlayerClub.team_id == 1)
                      .values(gameweeks=Range(3, None)))
        self.assertEqual(self.get("/v1/players/1")["clubs"][-1],
                         {"team": "ARS", "first_gameweek": 3, "last_gameweek": None})

    def test_a_player_not_in_the_season_is_404(self):
        self.get("/v1/players/999", 404)

    def test_models_show_the_gameweeks_they_missed(self):
        models = {m["model_version"]: m for m in self.get("/v1/models")["models"]}
        self.assertEqual(models["baseline-v1"]["gameweeks_logged"], [6, 8])
        self.assertEqual(models["baseline-v1"]["gameweeks_scored"], [6])
        self.assertEqual(models["baseline-v1"]["gameweeks_missed"], [7])
        self.assertEqual(models["form-fixture-v1"]["gameweeks_missed"], [])

    def test_the_scorecard_pools_rows_and_lists_each_gameweek(self):
        body = self.get("/v1/scorecard")
        self.assertEqual(body["harness_versions"], ["harness-v1"])
        baseline = {m["model_version"]: m for m in body["models"]}["baseline-v1"]
        self.assertEqual([g["gameweek"] for g in baseline["per_gameweek"]], [6])
        self.assertEqual(baseline["pooled"]["all"]["n"], 3)
        unscored = {m["model_version"]: m for m in body["models"]}["form-fixture-v1"]
        self.assertIsNone(unscored["pooled"]["all"])

    def test_an_unknown_season_is_404_and_a_malformed_one_422(self):
        self.get("/v1/gameweeks", 404, season="1999/00")
        self.get("/v1/gameweeks", 422, season="2026-27")

    def test_reads_carry_an_etag_and_a_repeat_gets_304(self):
        first = self.client.get("/v1/scorecard")
        self.assertEqual(first.headers["cache-control"], "public, max-age=300")
        again = self.client.get("/v1/scorecard",
                                headers={"If-None-Match": first.headers["etag"]})
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.content, b"")

    def test_readyz_is_ok_at_the_latest_migration(self):
        self.assertEqual(self.get("/readyz")["migration"], "0001")


class TestTheCommittedScorecard(APICase):
    """The real log: the API's scorecard agrees with scores/cumulative.json."""

    def test_pooled_metrics_and_missed_gameweeks_match_cumulative_json(self):
        os.chdir(ROOT)
        cumulative = json.loads((ROOT / "scores" / "cumulative.json").read_text())
        log = read_log()
        with self.engine.begin() as c:
            c.execute(insert(Season).values(season=SEASON))
            settled = max(gw for m in cumulative["models"].values()
                          for gw in m["gameweeks_scored"] + m["gameweeks_missed"])
            c.execute(insert(Gameweek), [
                {"season": SEASON, "gameweek": gw, "finished": True, "data_checked": True,
                 "deadline_utc": datetime(2026, 8, 1, tzinfo=UTC)}
                for gw in range(1, settled + 1)])
        project(self.engine, log)

        served = {m["model_version"]: m for m in self.get("/v1/scorecard")["models"]}
        self.assertEqual(set(served), set(cumulative["models"]))
        for model, expected in cumulative["models"].items():
            with self.subTest(model=model):
                got = served[model]
                self.assertEqual(got["gameweeks_missed"], expected["gameweeks_missed"])
                self.assertEqual(got["gameweeks_scored"], expected["gameweeks_scored"])
                for block in ("all", "predicted_to_play"):
                    for metric, value in expected["pooled"][block].items():
                        self.assertAlmostEqual(got["pooled"][block][metric], value, places=4)
                self.assertAlmostEqual(got["pooled"]["minutes_mae"],
                                       expected["pooled"]["minutes_mae"], places=4)
