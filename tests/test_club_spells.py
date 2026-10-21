"""Tests for fpl.db.load's pure parts: club spells and the season. No database needed."""

import unittest

from sqlalchemy.dialects.postgresql import Range

from fpl.db.load import club_spells, season_of

FIXTURES = {
    11: {"id": 11, "team_h": 1, "team_a": 2},
    21: {"id": 21, "team_h": 3, "team_a": 1},
    22: {"id": 22, "team_h": 2, "team_a": 3},
}


def played(fixture, round_, home):
    return {"fixture": fixture, "round": round_, "was_home": home}


class TestClubSpells(unittest.TestCase):

    def test_one_club_all_season(self):
        history = [played(11, 1, True), played(21, 2, False)]
        self.assertEqual(club_spells(history, FIXTURES, 1, 3), [(1, Range(1, 39))])

    def test_a_move_seen_in_the_fixtures(self):
        history = [played(11, 1, False), played(21, 2, True)]
        self.assertEqual(club_spells(history, FIXTURES, 3, 3),
                         [(2, Range(1, 2)), (3, Range(2, 39))])

    def test_a_move_after_the_last_fixture_starts_at_the_next_gameweek(self):
        history = [played(11, 1, False)]
        self.assertEqual(club_spells(history, FIXTURES, 1, 3),
                         [(2, Range(1, 3)), (1, Range(3, 39))])

    def test_a_new_signing_with_no_history_starts_at_the_next_gameweek(self):
        self.assertEqual(club_spells([], FIXTURES, 2, 8), [(2, Range(8, 39))])

    def test_a_move_inside_one_gameweek_gives_it_to_the_later_club(self):
        history = [played(11, 1, True), played(21, 2, False), played(22, 2, True)]
        self.assertEqual(club_spells(history, FIXTURES, 2, 3),
                         [(1, Range(1, 2)), (2, Range(2, 39))])

    def test_a_move_after_the_season_ends_adds_nothing(self):
        history = [played(11, 1, True)]
        self.assertEqual(club_spells(history, FIXTURES, 2, None), [(1, Range(1, 39))])


class TestSeason(unittest.TestCase):

    def test_named_for_the_year_of_its_first_deadline(self):
        events = [{"deadline_time": "2027-01-02T11:00:00Z"},
                  {"deadline_time": "2026-08-14T17:30:00Z"}]
        self.assertEqual(season_of(events), "2026/27")

    def test_the_turn_of_a_century_keeps_two_digits(self):
        self.assertEqual(season_of([{"deadline_time": "2099-08-14T17:30:00Z"}]), "2099/00")


if __name__ == "__main__":
    unittest.main()
