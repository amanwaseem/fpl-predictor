"""Tests for fpl-load: a synthetic snapshot into Postgres, and what loading again does.

Needs a database; see tests/database.py. Locally: make test-db
"""

import unittest
from decimal import Decimal

from alembic import command
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError

from fpl.db.load import load
from fpl.db.models import (
    Base,
    Player,
    PlayerClub,
    PlayerFixtureStats,
    SeasonPlayer,
    Snapshot,
)
from fpl.db.session import make_engine
from fpl.snapshot import load_snapshot
from tests.database import URL, alembic_config, empty_every_table, require_test_database
from tests.fixtures import TempCwd, element, event, team

FIRST = "20260828T080000Z"
SECOND = "20260829T080000Z"


def setUpModule():
    require_test_database()


def full_team(tid):
    return team(tid) | {"code": 100 + tid, "name": f"Team {tid}"}


def full_element(pid, **kwargs):
    return element(pid, **kwargs) | {"code": 1000 + pid, "first_name": f"First{pid}",
                                     "second_name": f"Second{pid}"}


def full_fixture(fid, gw, home, away, *, finished):
    return {"id": fid, "event": gw, "team_h": home, "team_a": away, "finished": finished,
            "kickoff_time": f"2026-08-{8 + gw * 7:02d}T14:00:00Z",
            "team_h_score": 2 if finished else None, "team_a_score": 1 if finished else None}


def history_row(pid, fixture, *, home, minutes=90, points=6):
    """A history row with every stat column the loader reads, as the API spells it."""
    row = {column.name: 0 for column in PlayerFixtureStats.__table__.columns}
    row.update({
        "element": pid, "fixture": fixture["id"], "round": fixture["event"], "was_home": home,
        "opponent_team": fixture["team_a"] if home else fixture["team_h"],
        "kickoff_time": fixture["kickoff_time"], "minutes": minutes, "total_points": points,
        "expected_goals": "0.42", "influence": "31.4", "value": 55, "starts": 1,
    })
    for key in ("season", "element_id", "fixture_id", "snapshot_id"):
        row.pop(key)
    return row


# Teams 1-3. GW1 and GW2 are played and settled; GW3 is next, and its one
# fixture has a zeroed history row, as a mid-gameweek snapshot would carry.
FIXTURES = [
    full_fixture(1, 1, 1, 2, finished=True),
    full_fixture(2, 2, 3, 1, finished=True),
    full_fixture(3, 3, 1, 3, finished=False),
]
EVENTS = [
    event(1, deadline="2026-08-14T17:30:00Z", finished=True, data_checked=True),
    event(2, deadline="2026-08-21T17:30:00Z", finished=True, data_checked=True),
    event(3, deadline="2026-08-28T17:30:00Z", is_next=True),
]


class TestLoad(TempCwd):

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

    def tearDown(self):
        empty_every_table(self.engine)
        super().tearDown()

    def write(self, name, *, elements=None, histories=None, fixtures=FIXTURES):
        if elements is None:
            elements = [
                full_element(1, team=1, web_name="Stays"),
                full_element(2, team=3, web_name="Moved"),
                full_element(3, team=1, web_name="Leaving", element_type=2),
                # FPL listed managers as elements in 2024/25: element_type 5.
                full_element(4, team=2, web_name="Manager", element_type=5),
            ]
        if histories is None:
            histories = {
                1: [history_row(1, FIXTURES[0], home=True),
                    history_row(1, FIXTURES[1], home=False),
                    history_row(1, FIXTURES[2], home=True, minutes=0, points=0)],
                2: [history_row(2, FIXTURES[0], home=False),
                    history_row(2, FIXTURES[1], home=True)],
                3: [history_row(3, FIXTURES[0], home=False)],
            }
        self.snapshot(name, elements=elements, teams=[full_team(t) for t in (1, 2, 3)],
                      events=EVENTS, fixtures=fixtures, histories=histories,
                      manifest={"snapshot_id": name, "element_count": len(elements),
                                "has_players": True, "live_gameweeks": [1, 2],
                                "completed_at_utc": "2026-08-28T08:06:00Z"})

    def load(self, name):
        return load(self.engine, *load_snapshot(name))

    def count(self, model):
        with self.engine.connect() as connection:
            return connection.scalar(select(func.count()).select_from(model))

    def scalar(self, statement):
        with self.engine.connect() as connection:
            return connection.scalar(statement)

    def rows(self, statement):
        with self.engine.connect() as connection:
            return [tuple(r) for r in connection.execute(statement)]

    def counts(self):
        return {name: self.count(table) for name, table in Base.metadata.tables.items()}

    def test_a_snapshot_fills_the_football_tables(self):
        self.write(FIRST)
        report = self.load(FIRST)
        self.assertEqual(report.season, "2026/27")
        counts = self.counts()
        self.assertEqual(counts["teams"], 3)
        self.assertEqual(counts["season_players"], 3)
        self.assertEqual(counts["gameweeks"], 3)
        self.assertEqual(counts["fixtures"], 3)
        self.assertEqual(counts["snapshots"], 1)

    def test_stats_come_only_from_finished_fixtures(self):
        self.write(FIRST)
        self.load(FIRST)
        self.assertEqual(self.count(PlayerFixtureStats), 5)
        self.assertIsNone(self.scalar(select(PlayerFixtureStats.minutes).where(
            PlayerFixtureStats.fixture_id == 3)))

    def test_strings_from_the_api_become_decimals(self):
        self.write(FIRST)
        self.load(FIRST)
        row = self.rows(select(PlayerFixtureStats.expected_goals,
                               PlayerFixtureStats.influence).limit(1))[0]
        self.assertEqual(row, (Decimal("0.42"), Decimal("31.4")))

    def test_club_spells_follow_the_fixtures_and_the_current_club(self):
        self.write(FIRST)
        self.load(FIRST)
        spells = self.rows(
            select(PlayerClub.element_id, PlayerClub.team_id, PlayerClub.gameweeks)
            .order_by(PlayerClub.element_id, PlayerClub.gameweeks))
        self.assertEqual(spells, [
            (1, 1, Range(1, 39)),
            (2, 2, Range(1, 2)), (2, 3, Range(2, 39)),
            (3, 2, Range(1, 3)), (3, 1, Range(3, 39)),
        ])

    def test_elements_that_are_not_players_are_skipped_and_reported(self):
        self.write(FIRST)
        report = self.load(FIRST)
        self.assertEqual(report.skipped_elements, [4])
        self.assertIn("Skipped 1", str(report))

    def test_loading_a_snapshot_again_changes_nothing(self):
        self.write(FIRST)
        self.load(FIRST)
        before = self.counts()
        report = self.load(FIRST)
        self.assertTrue(report.already_loaded)
        self.assertEqual(self.counts(), before)

    def test_a_newer_snapshot_updates_rows(self):
        self.write(FIRST)
        self.load(FIRST)
        renamed = [full_element(1, team=1, web_name="Renamed"),
                   full_element(2, team=3, web_name="Moved"),
                   full_element(3, team=1, web_name="Leaving", element_type=2)]
        self.write(SECOND, elements=renamed)
        self.assertTrue(self.load(SECOND).newest)
        self.assertEqual(self.scalar(select(SeasonPlayer.web_name).where(
            SeasonPlayer.element_id == 1)), "Renamed")
        self.assertEqual(self.scalar(select(func.max(PlayerFixtureStats.snapshot_id))), SECOND)

    def test_an_older_snapshot_only_fills_gaps(self):
        older = [full_element(1, team=1, web_name="Renamed"),
                 full_element(5, team=2, web_name="OnlyInOlder")]
        self.write(SECOND)
        self.write(FIRST, elements=older,
                   histories={1: [history_row(1, FIXTURES[0], home=True, points=1)]})
        self.load(SECOND)
        report = self.load(FIRST)
        self.assertFalse(report.newest)
        names = dict(self.rows(select(SeasonPlayer.element_id, SeasonPlayer.web_name)))
        self.assertEqual(names[1], "Stays")
        self.assertEqual(names[5], "OnlyInOlder")
        self.assertEqual(self.rows(
            select(PlayerFixtureStats.total_points, PlayerFixtureStats.snapshot_id)
            .where(PlayerFixtureStats.element_id == 1, PlayerFixtureStats.fixture_id == 1)),
            [(6, SECOND)])
        spells = self.rows(select(PlayerClub.element_id, PlayerClub.team_id)
                           .where(PlayerClub.element_id.in_([1, 5])))
        self.assertEqual(sorted(spells), [(1, 1), (5, 2)])

    def test_an_older_season_does_not_rename_players(self):
        self.write(FIRST)
        self.load(FIRST)
        last_season = [event(gw, deadline=f"2025-08-{8 + gw * 7:02d}T17:30:00Z",
                             finished=True, data_checked=True) for gw in (1, 2, 3)]
        self.snapshot("20250901T080000Z",
                      elements=[full_element(1, team=1, web_name="Old") | {"first_name": "Old"}],
                      teams=[full_team(1)], events=last_season, fixtures=[], histories={1: []},
                      manifest={"snapshot_id": "20250901T080000Z", "element_count": 1,
                                "has_players": True, "completed_at_utc": "2025-09-01T08:06:00Z"})
        self.assertTrue(self.load("20250901T080000Z").newest)
        self.assertEqual(self.scalar(select(Player.first_name).where(Player.code == 1001)),
                         "First1")

    def test_a_load_that_fails_leaves_nothing_behind(self):
        broken = history_row(1, FIXTURES[0], home=True)
        broken["minutes"] = None
        self.write(FIRST, histories={1: [broken]})
        with self.assertRaises(IntegrityError):
            self.load(FIRST)
        self.assertEqual(self.count(Snapshot), 0)
        self.assertEqual(self.count(SeasonPlayer), 0)

    def test_the_manifest_is_recorded(self):
        self.write(FIRST)
        self.load(FIRST)
        self.assertEqual(self.rows(select(Snapshot.element_count, Snapshot.live_gameweeks)),
                         [(4, [1, 2])])


if __name__ == "__main__":
    unittest.main()
