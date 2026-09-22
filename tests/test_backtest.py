"""Tests for fpl/backtest.py, the offline replay of settled gameweeks.

The first class is the one that matters. A backtest that leaks tunes models
toward information they will never have at a real deadline, so the leak test
poisons everything after the cut — later rounds, current player state, the
target gameweek's results — and demands byte-identical predictions.

Run from the repository root: python -m unittest discover tests
"""

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
from pathlib import Path

from fpl import backtest, predict_baseline
from fpl.snapshot import load_snapshot, usable_rounds
from tests import fixtures

LATER = "20260922T000000Z"
CLUBS = 20
PER_CLUB = 4


def fixture_id(gw, pair):
    return gw * 100 + pair


def build_season(root, name=LATER, *, played_through=4, target=5, live_gws=(2, 3, 4),
                 latest=True):
    """A season played through `played_through`, with real-shaped history.

    Unlike fixtures.league_snapshot, every round has fixtures with results and
    every history row names its fixture, home flag and price, which is what the
    replay reads a player's club and price from.
    """
    teams = [fixtures.team(t) for t in range(1, CLUBS + 1)]
    fx = []
    for gw in range(1, target + 1):
        for pair in range(CLUBS // 2):
            home, away = 2 * pair + 1, 2 * pair + 2
            if gw % 2 == 0:
                home, away = away, home
            played = gw <= played_through
            fx.append({
                "id": fixture_id(gw, pair), "event": gw, "team_h": home, "team_a": away,
                "team_h_score": (gw + pair) % 3 if played else None,
                "team_a_score": pair % 2 if played else None,
                "finished": played, "started": played, "stats": [],
                "kickoff_time": f"2026-08-{14 + gw:02d}T14:00:00Z",
                "team_h_difficulty": 3, "team_a_difficulty": 3,
            })

    elements, histories, pid = [], {}, 0
    for club in range(1, CLUBS + 1):
        for slot in range(PER_CLUB):
            pid += 1
            elements.append(fixtures.element(pid, team=club, element_type=slot + 1,
                                             now_cost=45 + slot * 5))
            rows = []
            for gw in range(1, played_through + 1):
                pair = (club - 1) // 2
                f = next(x for x in fx if x["id"] == fixture_id(gw, pair))
                rows.append({
                    "round": gw, "fixture": f["id"], "was_home": f["team_h"] == club,
                    "minutes": 90 - slot * 25, "total_points": (pid + gw) % 7,
                    "value": 45 + slot * 5 + gw,
                })
            histories[pid] = rows

    directory = fixtures.snapshot(
        root, name, elements=elements, teams=teams,
        events=fixtures.season(target, fixtures.future_deadline(),
                               checked_through=played_through),
        fixtures=fx, histories=histories, latest=latest,
        manifest={"snapshot_id": name, "element_count": len(elements),
                  "has_players": True, "live_gameweeks": list(live_gws)},
    )
    for gw in live_gws:
        fixtures.live(directory, gw, {p: ((p + gw) % 7, 90) for p in histories})
    return directory


def read(directory):
    snap, bootstrap, fx, players_dir = load_snapshot(directory.name)
    histories = {p["id"]: json.loads((players_dir / f"{p['id']}.json").read_text())["history"]
                 for p in bootstrap["elements"]}
    return bootstrap, fx, histories


def replay_rows(bootstrap, fx, histories, gw, model="baseline-v1"):
    return backtest.MODELS[model](*backtest.as_of(bootstrap, fx, histories, gw), gw)


class TestNoLeak(fixtures.TempCwd):
    """Hard rule 2, for the backtest: nothing at or after GW N reaches GW N."""

    def setUp(self):
        super().setUp()
        self.bootstrap, self.fx, self.histories = read(build_season(self.tmp))

    def test_poisoning_the_future_changes_nothing(self):
        target = 3
        clean = replay_rows(self.bootstrap, self.fx, self.histories, target)

        bootstrap = copy.deepcopy(self.bootstrap)
        fx = copy.deepcopy(self.fx)
        histories = copy.deepcopy(self.histories)
        for rows in histories.values():
            for row in rows:
                if row["round"] >= target:
                    row.update(minutes=90, total_points=99, value=999)
        for player in bootstrap["elements"]:
            # Everything a later snapshot knows about a player "now".
            player.update(status="i", chance_of_playing_next_round=0, form="99.0",
                          points_per_game="99.0", now_cost=999,
                          team=(player["team"] % CLUBS) + 1, total_points=999)
        for f in fx:
            if f["event"] >= target:
                f.update(team_h_score=9, team_a_score=0, finished=True, stats=["poison"])
        for event in bootstrap["events"]:
            if event["id"] >= target:
                event.update(finished=True, data_checked=True)

        self.assertEqual(replay_rows(bootstrap, fx, histories, target), clean)

    def test_the_poison_would_have_been_noticed(self):
        """Guard the guard: the same poison before the cut does change the output."""
        target = 3
        clean = replay_rows(self.bootstrap, self.fx, self.histories, target)
        histories = copy.deepcopy(self.histories)
        for rows in histories.values():
            for row in rows:
                if row["round"] == target - 1:
                    row.update(minutes=90, total_points=99)
        self.assertNotEqual(replay_rows(self.bootstrap, self.fx, histories, target), clean)


class TestView(fixtures.TempCwd):
    """What as_of lets through, field by field."""

    def setUp(self):
        super().setUp()
        self.bootstrap, self.fx, self.histories = read(build_season(self.tmp))

    def view(self, gw=3):
        return backtest.as_of(self.bootstrap, self.fx, self.histories, gw)

    def test_player_fields_are_whitelisted(self):
        self.bootstrap["elements"][0]["ep_next"] = "9.9"
        view, _, _ = self.view()
        self.assertEqual(set(view["elements"][0]),
                         set(backtest.PLAYER_FIELDS) | {"status", "chance_of_playing_next_round"})

    def test_everyone_is_available(self):
        self.bootstrap["elements"][0].update(status="i", chance_of_playing_next_round=0)
        view, _, _ = self.view()
        self.assertEqual({(e["status"], e["chance_of_playing_next_round"])
                          for e in view["elements"]}, {("a", None)})

    def test_club_is_read_from_the_last_fixture_not_the_current_team(self):
        """A player transferred after GW2 is still at his old club for GW3."""
        self.bootstrap["elements"][0]["team"] = 17
        view, _, _ = self.view()
        self.assertEqual(view["elements"][0]["team"], 1)

    def test_price_is_the_last_round_value(self):
        view, _, _ = self.view(gw=3)
        # Player 1, slot 0: value 45 + gw, last round before GW3 is GW2.
        self.assertEqual(view["elements"][0]["now_cost"], 47)

    def test_history_is_cut_before_the_target(self):
        _, _, histories = self.view(gw=3)
        self.assertEqual({r["round"] for rows in histories.values() for r in rows}, {1, 2})

    def test_a_player_who_had_not_joined_is_left_out(self):
        self.histories[5] = [r for r in self.histories[5] if r["round"] >= 3]
        view, _, histories = self.view(gw=3)
        self.assertNotIn(5, {e["id"] for e in view["elements"]})
        self.assertNotIn(5, histories)

    def test_target_fixtures_lose_their_results_and_later_ones_go(self):
        _, fx, _ = self.view(gw=3)
        self.assertEqual({f["event"] for f in fx}, {1, 2, 3})
        target = [f for f in fx if f["event"] == 3]
        self.assertTrue(target)
        for f in target:
            self.assertEqual(set(f), set(backtest.UPCOMING_FIXTURE_FIELDS))
        self.assertIn("team_h_score", next(f for f in fx if f["event"] == 2))

    def test_events_end_at_the_target(self):
        view, _, _ = self.view(gw=3)
        events = {e["id"]: e for e in view["events"]}
        self.assertEqual(sorted(events), [1, 2, 3])
        self.assertTrue(events[3]["is_next"])
        self.assertFalse(events[3]["finished"] or events[3]["data_checked"])

    def test_missing_club_or_price_fields_stop_the_run(self):
        """No silent fallback to the current club and price: that would leak."""
        for field in backtest.HISTORY_FIELDS:
            with self.subTest(field=field):
                histories = copy.deepcopy(self.histories)
                for row in histories[1]:
                    del row[field]
                with self.assertRaises(SystemExit) as caught:
                    backtest.as_of(self.bootstrap, self.fx, histories, 3)
                self.assertIn(field, str(caught.exception))

    def test_unknown_fixture_stops_the_run(self):
        histories = copy.deepcopy(self.histories)
        # GW2 is the last round before the cut at GW3, so it is the row read.
        next(r for r in histories[1] if r["round"] == 2)["fixture"] = 99999
        with self.assertRaises(SystemExit):
            backtest.as_of(self.bootstrap, self.fx, histories, 3)

    def test_the_originals_are_not_modified(self):
        before = copy.deepcopy((self.bootstrap, self.fx, self.histories))
        self.view()
        self.assertEqual((self.bootstrap, self.fx, self.histories), before)


class TestSameModel(fixtures.TempCwd):

    def test_replay_matches_the_baseline_run_at_the_time(self):
        """A snapshot taken before GW3 and a replay of GW3 from later agree."""
        later = build_season(self.tmp, played_through=4, target=5, latest=False)
        at_time = build_season(self.tmp, "20260830T000000Z", played_through=2, target=3,
                               live_gws=(2,), latest=False)
        bootstrap, fx, histories = read(at_time)
        usable, _ = usable_rounds(bootstrap["events"], 3)
        expected = predict_baseline.predict_rows(bootstrap, fx, histories, 3, usable)

        replayed = replay_rows(*read(later), 3)
        keys = ("player_id", "predicted_points", "expected_minutes", "points_per_90",
                "team", "n_fixtures")
        self.assertEqual([{k: r[k] for k in keys} for r in replayed],
                         [{k: r[k] for k in keys} for r in expected])


class TestRun(fixtures.TempCwd):

    def setUp(self):
        super().setUp()
        build_season(self.tmp)

    def test_default_replays_every_settled_gameweek_with_actuals(self):
        report = backtest.run(None, "baseline-v1")
        self.assertEqual(report["gameweeks"], [2, 3, 4])
        self.assertEqual(report["pooled"]["all"]["n"],
                         sum(r["metrics"]["all"]["n"] for r in report["per_gameweek"]))
        self.assertIn("not evidence", report["note"])

    def test_comparators_are_rebuilt_from_history(self):
        report = backtest.run(None, "baseline-v1", [3])
        self.assertEqual(set(report["per_gameweek"][0]["comparators"]),
                         {"zero", "history_ppg", "last_3_mean"})

    def test_history_comparator_values(self):
        values = backtest._comparator_values({1: [
            {"round": 1, "minutes": 90, "total_points": 2},
            {"round": 2, "minutes": 0, "total_points": 0},
            {"round": 3, "minutes": 90, "total_points": 4},
            {"round": 4, "minutes": 90, "total_points": 9},
        ]})[1]
        self.assertEqual(values["history_ppg"], 5.0)   # (2+4+9)/3 appearances
        self.assertAlmostEqual(values["last_3_mean"], 13 / 3)  # rounds 4, 3, 2

    def test_last_three_counts_rounds_not_rows(self):
        """A double is one round: its fixtures sum, and the window stays 3 rounds."""
        values = backtest._comparator_values({1: [
            {"round": 1, "minutes": 90, "total_points": 6},
            {"round": 2, "minutes": 90, "total_points": 2},
            {"round": 3, "minutes": 90, "total_points": 5},   # double gameweek,
            {"round": 3, "minutes": 90, "total_points": 7},   # two rows
        ]})[1]
        self.assertAlmostEqual(values["last_3_mean"], (12 + 2 + 6) / 3)

    def test_refusals(self):
        for model, gameweeks in (("no-such-model", None), ("baseline-v1", [1]),
                                 ("baseline-v1", [5])):
            with self.subTest(model=model, gameweeks=gameweeks), \
                    self.assertRaises(SystemExit):
                backtest.run(None, model, gameweeks)

    def test_output_never_goes_into_predictions(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            backtest.main(None, "baseline-v1", None, None, "predictions")
        self.assertFalse(Path("predictions").exists())

    def test_from_and_to_go_together(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            backtest.main(None, "baseline-v1", 2, None, "scratch")

    def test_command_line(self):
        repo = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "-m", "fpl.backtest", "--from", "2", "--to", "3"],
            capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(repo)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("BACKTEST — A backtest is not evidence"))
        written = json.loads(Path(f"scratch/backtest_{LATER}_baseline-v1.json").read_text())
        self.assertEqual(written["gameweeks"], [2, 3])
