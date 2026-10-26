"""Reading and shaping the past-season archive, fpl.ingest.history (#79)."""

import hashlib
import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

from sqlalchemy.dialects.postgresql import Range

from fpl.ingest import history
from tests.history_files import gw_row, season_rows, write_season

NAMES = {3: "ARS", 88: "HUL"}


class ArchiveCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = Path(self.tmp.name)

    def parse(self, season="2016/17", **kwargs):
        write_season(self.raw, season, **kwargs)
        return history.parse_season(season, self.raw, NAMES)


class TestParseSeason(ArchiveCase):
    def test_fixtures_come_from_both_sides_player_rows(self):
        fixtures = {f["fixture_id"]: f for f in self.parse().fixtures}
        self.assertEqual((fixtures[10]["team_h"], fixtures[10]["team_a"]), (1, 2))
        self.assertEqual((fixtures[20]["team_h"], fixtures[20]["team_a"]), (2, 1))
        self.assertEqual((fixtures[10]["team_h_score"], fixtures[10]["team_a_score"]), (2, 1))
        self.assertTrue(all(f["finished"] for f in fixtures.values()))

    def test_a_deadline_is_ninety_minutes_before_the_first_kickoff(self):
        gameweeks = {g["gameweek"]: g for g in self.parse().gameweeks}
        self.assertEqual(gameweeks[1]["deadline_utc"], datetime(2016, 8, 13, 12, 30, tzinfo=UTC))
        self.assertEqual(sorted(gameweeks), [1, 2])

    def test_managers_are_not_players(self):
        parsed = self.parse()
        self.assertEqual(parsed.not_players, 1)
        self.assertNotIn(9, {p["element_id"] for p in parsed.season_players})
        self.assertNotIn(9, {s["element_id"] for s in parsed.player_fixture_stats})

    def test_a_move_between_clubs_is_two_spells(self):
        spells = [
            (c["team_id"], c["gameweeks"])
            for c in self.parse().player_clubs
            if c["element_id"] == 3
        ]
        self.assertEqual(spells, [(1, Range(1, 2)), (2, Range(2, 39))])

    def test_stats_take_the_columns_types(self):
        row = next(s for s in self.parse().player_fixture_stats if s["element_id"] == 1)
        self.assertIs(row["was_home"], True)
        self.assertEqual(row["total_points"], 5)
        self.assertEqual(row["influence"], Decimal("12.4"))
        self.assertIsNone(row["expected_goals"])
        self.assertIsNone(row["snapshot_id"])

    def test_teams_short_names_come_from_codes(self):
        teams = {t["team_id"]: t for t in self.parse().teams}
        self.assertEqual(
            (teams[2]["code"], teams[2]["short_name"], teams[2]["name"]), (88, "HUL", "Hull")
        )

    def test_a_cp1252_file_is_read(self):
        players, gws = season_rows()
        gws[0]["name"] = "Édouard"  # 0xC9 then a letter: cp1252, and not UTF-8
        parsed = self.parse(gws=gws, encoding="cp1252")
        with self.assertRaises(UnicodeDecodeError):
            (self.raw / "2016-17" / "gws" / "merged_gw.csv").read_bytes().decode("utf-8")
        self.assertEqual(len(parsed.player_fixture_stats), 6)

    def test_a_postponed_fixtures_stale_row_gives_way_to_its_result(self):
        players, gws = season_rows()
        stale = gw_row(1, 20, 2, home=False, opponent=2, kickoff="2016-08-20T11:30:00Z")
        stale["team_h_score"] = stale["team_a_score"] = ""
        gws.insert(4, stale)
        parsed = self.parse(gws=gws)
        self.assertEqual(parsed.duplicates, 1)
        fixture = next(f for f in parsed.fixtures if f["fixture_id"] == 20)
        self.assertEqual(fixture["team_h_score"], 0)

    def test_the_2019_20_restart_is_renumbered_into_38_gameweeks(self):
        players, gws = season_rows()
        for row in gws[4:]:
            row["round"] = row["GW"] = 39
        parsed = self.parse("2019/20", gws=gws, teams_csv=True)
        self.assertEqual(sorted(g["gameweek"] for g in parsed.gameweeks), [1, 30])
        self.assertEqual(history.gameweek_of("2018/19", 39), 39)

    def test_one_sided_fixture_is_refused(self):
        players, gws = season_rows()
        with self.assertRaises(SystemExit):
            self.parse(gws=[gws[0]])


class TestFiles(ArchiveCase):
    def response(self, content):
        return mock.Mock(content=content, raise_for_status=mock.Mock())

    def test_a_file_whose_hash_differs_is_refused_and_not_written(self):
        files = {"a.csv": hashlib.sha256(b"pinned").hexdigest()}
        with (
            mock.patch.object(history, "FILES", files),
            mock.patch.object(history.requests, "get", return_value=self.response(b"other")),
        ):
            with self.assertRaises(SystemExit) as raised:
                history.download(self.raw)
        self.assertIn("not the pinned", str(raised.exception))
        self.assertFalse((self.raw / "a.csv").exists())

    def test_a_file_already_there_and_matching_is_not_fetched(self):
        (self.raw / "a.csv").write_bytes(b"pinned")
        files = {"a.csv": hashlib.sha256(b"pinned").hexdigest()}
        with (
            mock.patch.object(history, "FILES", files),
            mock.patch.object(history.requests, "get") as get,
        ):
            self.assertEqual(history.download(self.raw), [])
        get.assert_not_called()

    def test_verify_names_a_missing_or_changed_file(self):
        files = {"a.csv": hashlib.sha256(b"pinned").hexdigest()}
        with mock.patch.object(history, "FILES", files):
            with self.assertRaises(SystemExit):
                history.verify(self.raw)
            (self.raw / "a.csv").write_bytes(b"changed")
            with self.assertRaises(SystemExit):
                history.verify(self.raw)

    def test_every_season_and_the_team_list_are_pinned(self):
        self.assertIn("master_team_list.csv", history.FILES)
        for season in history.SEASONS:
            folder = history.folder(season)
            with self.subTest(season=season):
                self.assertIn(f"{folder}/players_raw.csv", history.FILES)
                self.assertIn(f"{folder}/gws/merged_gw.csv", history.FILES)


if __name__ == "__main__":
    unittest.main()
