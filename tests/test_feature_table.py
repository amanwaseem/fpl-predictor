"""The feature table's builder, fpl.models.feature_table (#84).

The leak test is the backtest's (tests/test_backtest.py, TestNoLeak) applied to
this pipeline: corrupt everything at or after the cut, and the rows for the
gameweek must not change; corrupt the round before, and they must.
"""

import copy
import random
import unittest

from fpl.models import feature_table
from fpl.models.feature_table import SeasonData, build_gameweek

CLUBS = 4
GAMEWEEKS = 6


def season(seed=7, with_xg=True):
    """Four clubs, two players each, a round robin over six gameweeks; GW1-5 settled."""
    rng = random.Random(seed)
    pairs = [(1, 2), (3, 4), (1, 3), (2, 4), (1, 4), (2, 3)]
    fixtures = []
    for gw in range(1, GAMEWEEKS + 1):
        h, a = pairs[gw - 1]
        others = tuple(sorted(set(range(1, CLUBS + 1)) - {h, a}))
        for fid, (home, away) in enumerate([(h, a), others], start=gw * 10):
            fixtures.append(
                {
                    "id": fid,
                    "event": gw,
                    "team_h": home,
                    "team_a": away,
                    "team_h_score": rng.randint(0, 3),
                    "team_a_score": rng.randint(0, 3),
                    "finished": gw < GAMEWEEKS,
                }
            )
    players = {
        pid: {"position": "MID" if pid % 2 else "DEF", "code": 1000 + pid}
        for pid in range(1, 2 * CLUBS + 1)
    }
    clubs = {pid: [((pid + 1) // 2, 1, 38)] for pid in players}
    histories = {pid: [] for pid in players}
    for f in fixtures:
        for pid, spells in clubs.items():
            team = spells[0][0]
            if team in (f["team_h"], f["team_a"]):
                minutes = rng.choice([0, 25, 90])
                histories[pid].append(
                    {
                        "round": f["event"],
                        "fixture": f["id"],
                        "was_home": team == f["team_h"],
                        "minutes": minutes,
                        "total_points": rng.randint(0, 9),
                        "bonus": rng.randint(0, 3),
                        "value": 50 + f["event"],
                        "expected_goals": f"{rng.random():.2f}" if with_xg else None,
                        "expected_assists": f"{rng.random():.2f}" if with_xg else None,
                    }
                )
    return SeasonData(
        season="2026/27",
        fixtures=fixtures,
        histories=histories,
        settled=set(range(1, GAMEWEEKS)),
        clubs=clubs,
        players=players,
        previous={1001: {"minutes": 2000, "points": 120}},
        status={(3, 1): "d"},
    )


def poison_from(data, cut):
    """Everything a later view of the season knows, at or after `cut`."""
    data = copy.deepcopy(data)
    for rows in data.histories.values():
        for row in rows:
            if row["round"] >= cut:
                row.update(
                    minutes=90,
                    total_points=99,
                    bonus=3,
                    value=999,
                    expected_goals="9.00",
                    expected_assists="9.00",
                )
    for f in data.fixtures:
        if f["event"] >= cut:
            f.update(team_h_score=9, team_a_score=0, finished=True)
    data.settled |= set(range(cut, GAMEWEEKS + 1))
    data.status |= {(gw, pid): "i" for gw in range(cut + 1, GAMEWEEKS + 1) for pid in data.players}
    return data


class TestNoLeak(unittest.TestCase):
    """Hard rule 2, for the feature table: nothing at or after GW N reaches GW N."""

    def test_poisoning_the_future_changes_nothing(self):
        for with_xg in (True, False):
            data = season(with_xg=with_xg)
            for gameweek in range(1, GAMEWEEKS + 1):
                with self.subTest(gameweek=gameweek, with_xg=with_xg):
                    self.assertEqual(
                        build_gameweek(poison_from(data, gameweek), gameweek),
                        build_gameweek(data, gameweek),
                    )

    def test_the_poison_would_have_been_noticed(self):
        """Guard the guard: the same poison one round earlier does change the rows."""
        data = season()
        for gameweek in range(2, GAMEWEEKS + 1):
            with self.subTest(gameweek=gameweek):
                self.assertNotEqual(
                    build_gameweek(poison_from(data, gameweek - 1), gameweek),
                    build_gameweek(data, gameweek),
                )

    def test_an_unsettled_round_is_not_read(self):
        data = season()
        data.settled.discard(4)
        poisoned = poison_from(data, 4)
        poisoned.settled -= {4, 5, 6}
        # GW5's own entry may say who is available for GW5; only results are poison here.
        poisoned.status = data.status
        self.assertEqual(build_gameweek(poisoned, 5), build_gameweek(data, 5))


class TestRows(unittest.TestCase):
    def setUp(self):
        self.data = season()

    def rows(self, gameweek):
        return {r["element_id"]: r for r in build_gameweek(self.data, gameweek)}

    def test_every_player_whose_club_plays_has_a_row(self):
        self.assertEqual(sorted(self.rows(3)), list(range(1, 2 * CLUBS + 1)))

    def test_gw1_has_no_form_no_price_and_no_ratings(self):
        row = self.rows(1)[1]
        self.assertIsNone(row["minutes_last1"])
        self.assertIsNone(row["price"])
        self.assertIsNone(row["team_attack"])
        self.assertEqual(row["season_minutes"], 0)

    def test_the_price_is_the_last_one_before_the_gameweek(self):
        self.assertEqual(self.rows(4)[1]["price"], 53)

    def test_windows_are_the_most_recent_rounds(self):
        history = [h for h in self.data.histories[1] if h["round"] < 4]
        latest = max(h["round"] for h in history)
        row = self.rows(4)[1]
        self.assertEqual(
            row["minutes_last1"], sum(h["minutes"] for h in history if h["round"] == latest)
        )
        self.assertEqual(row["season_minutes"], sum(h["minutes"] for h in history))

    def test_last_season_is_joined_by_code(self):
        row = self.rows(2)[1]
        self.assertEqual(
            (row["last_season_minutes"], row["last_season_points_per90"]), (2000, 120 / 2000 * 90)
        )
        self.assertIsNone(self.rows(2)[2]["last_season_minutes"])

    def test_status_comes_from_the_entry_for_that_gameweek_only(self):
        self.assertEqual(self.rows(3)[1]["status"], "d")
        self.assertIsNone(self.rows(4)[1]["status"])

    def test_a_season_without_xg_is_rated_on_goals_and_has_no_xg_rates(self):
        self.data = season(with_xg=False)
        row = self.rows(4)[1]
        self.assertIsNotNone(row["team_attack"])
        self.assertIsNone(row["xg_per90_5"])

    def test_a_player_whose_club_does_not_play_has_no_row(self):
        self.data.clubs[1] = [(9, 1, 38)]
        self.assertNotIn(1, self.rows(3))

    def test_the_version_is_on_every_row(self):
        self.assertTrue(
            all(
                r["feature_version"] == feature_table.FEATURE_VERSION
                for r in build_gameweek(self.data, 3)
            )
        )


if __name__ == "__main__":
    unittest.main()
