"""Team news from bootstrap-static, fpl.api.news (#196)."""

import unittest

from fastapi import HTTPException

from fpl.api import news


def element(id, status, chance=None, owned="1.0", **extra):
    return {
        "id": id,
        "code": 1000 + id,
        "web_name": f"P{id}",
        "team": 1,
        "element_type": 3,
        "status": status,
        "chance_of_playing_next_round": chance,
        "news": f"news {id}",
        "news_added": "2026-11-20T15:30:12.123456Z",
        "selected_by_percent": owned,
    } | extra


def bootstrap(*elements):
    return {"teams": [{"id": 1, "short_name": "ARS"}], "elements": list(elements)}


class TestDoubts(unittest.TestCase):
    def test_lists_who_may_miss_the_next_gameweek_and_no_one_else(self):
        found = news.doubts(
            bootstrap(
                element(1, "a"),
                element(2, "d", 75),
                element(3, "i", 0),
                element(4, "s", 0),
                element(5, "n", 0),
                element(6, "u", 0),
            )
        )
        self.assertEqual([i["player_id"] for i in found], [2, 3, 4, 5])

    def test_the_most_owned_come_first(self):
        found = news.doubts(bootstrap(element(1, "d", owned="2.5"), element(2, "i", owned="40.1")))
        self.assertEqual([i["player_id"] for i in found], [2, 1])

    def test_a_row_carries_club_position_chance_and_news(self):
        (row,) = news.doubts(bootstrap(element(2, "d", 75)))
        self.assertEqual(
            {k: row[k] for k in ("team", "position", "chance_of_playing", "news")},
            {"team": "ARS", "position": "MID", "chance_of_playing": 75, "news": "news 2"},
        )

    def test_no_chance_given_stays_unknown(self):
        (row,) = news.doubts(bootstrap(element(2, "i", None, news=None)))
        self.assertIsNone(row["chance_of_playing"])
        self.assertEqual(row["news"], "")

    def test_a_changed_schema_is_fpls_problem_not_the_requests(self):
        for broken in ({}, {"teams": [], "elements": [element(2, "d")]}, {"teams": None}):
            with self.subTest(bootstrap=broken), self.assertRaises(HTTPException) as caught:
                news.doubts(broken)
            self.assertEqual(caught.exception.status_code, 502)
