"""Tests for snapshot integrity and the rule 2 backtest guard.

These cover the code paths added by the integrity work itself. The first
version of that work shipped with 16 tests, none of which touched any of it —
and review found two high-severity bugs living in exactly the untested parts.
Tests here run against a temporary data/raw, never the real one.

Run from the repository root: python -m unittest discover tests
"""

import json
import unittest

import fixtures
from fpl import fetch
from fpl.snapshot import load_snapshot, resolve_target_gw


class TempCwd(fixtures.TempCwd):
    """A snapshot in the minimal shape these tests were written against.

    The builder itself now lives in tests/fixtures.py, shared with the
    verifier and end-to-end suites. This wrapper keeps the argument shape
    these tests use: `players` is how many per-player history files exist,
    which is separate from how many elements the bootstrap claims — that gap
    is exactly what the missing-players check is about.
    """

    def snap(self, name, *, players=0, manifest=None, elements=None):
        ids = elements if elements is not None else list(range(1, players + 1))
        return self.snapshot(
            name,
            elements=[fixtures.element(pid) for pid in ids],
            player_ids=list(range(1, players + 1)),
            manifest=manifest,
        )


class TestFindIncomplete(TempCwd):
    def test_picks_interrupted_full_fetch(self):
        self.snap("20260910T000000Z", players=2)
        self.assertEqual(fetch.find_incomplete().name, "20260910T000000Z")

    def test_ignores_bootstrap_only_snapshot(self):
        """No players/ content means a stale bootstrap, not a resumable fetch.

        Resuming one would pair today's player histories with an old
        bootstrap — the temporal mixing resume exists to avoid.
        """
        d = self.tmp / "data/raw/20260910T000000Z"
        d.mkdir(parents=True)
        (d / "bootstrap.json").write_text("{}")
        self.assertIsNone(fetch.find_incomplete())

    def test_ignores_snapshot_older_than_latest(self):
        self.snap("20260101T000000Z", players=2)
        (self.tmp / "data/raw/LATEST").write_text("20260905T000000Z")
        self.assertIsNone(fetch.find_incomplete())

    def test_ignores_completed_snapshot(self):
        self.snap("20260910T000000Z", players=2, manifest={"has_players": True})
        self.assertIsNone(fetch.find_incomplete())


class TestAtomicWrite(TempCwd):
    def test_no_temp_file_left_behind(self):
        d = self.tmp / "out"
        fetch.write(d, "a.json", {"x": 1})
        self.assertEqual([p.name for p in d.iterdir()], ["a.json"])

    def test_content_round_trips(self):
        d = self.tmp / "out"
        fetch.write(d, "a.json", {"x": 1})
        self.assertEqual(json.loads((d / "a.json").read_text()), {"x": 1})


class TestBacktestGuard(unittest.TestCase):
    """Hard rule 2: bootstrap fields are snapshot-time and unbounded."""

    FORWARD = [{"id": 1, "is_next": False, "finished": True, "deadline_time": "t"},
               {"id": 2, "is_next": False, "finished": True, "deadline_time": "t"},
               {"id": 3, "is_next": True, "finished": False, "deadline_time": "t"}]

    def test_past_gameweek_refused(self):
        with self.assertRaises(SystemExit):
            resolve_target_gw(self.FORWARD, 2)

    def test_next_gameweek_allowed(self):
        self.assertEqual(resolve_target_gw(self.FORWARD, 3)[0], 3)

    def test_future_gameweek_allowed(self):
        events = self.FORWARD + [{"id": 4, "is_next": False, "finished": False,
                                  "deadline_time": "t"}]
        self.assertEqual(resolve_target_gw(events, 4)[0], 4)

    def test_guard_holds_when_is_next_absent(self):
        """The bug review found: a missing flag skipped the check entirely."""
        events = [{"id": i, "finished": i < 3, "deadline_time": "t"}
                  for i in range(1, 5)]
        with self.assertRaises(SystemExit):
            resolve_target_gw(events, 2)

    def test_refuses_everything_when_season_over(self):
        events = [{"id": i, "finished": True, "deadline_time": "t"}
                  for i in range(1, 5)]
        with self.assertRaises(SystemExit):
            resolve_target_gw(events, 4)

    def test_unknown_gameweek_refused(self):
        with self.assertRaises(SystemExit):
            resolve_target_gw(self.FORWARD, 99)


class TestLoadSnapshot(TempCwd):
    def point_at(self, name):
        (self.tmp / "data/raw/LATEST").write_text(name)

    def test_missing_manifest_refused(self):
        self.snap("20260910T000000Z", players=2)
        self.point_at("20260910T000000Z")
        with self.assertRaises(SystemExit) as cm:
            load_snapshot()
        self.assertIn("manifest", str(cm.exception))

    def test_missing_player_files_refused(self):
        self.snap("20260910T000000Z", players=1, elements=[1, 2, 3],
                  manifest={"has_players": True, "element_count": 3})
        self.point_at("20260910T000000Z")
        with self.assertRaises(SystemExit) as cm:
            load_snapshot()
        self.assertIn("missing", str(cm.exception))

    def test_skip_players_snapshot_refused_with_right_advice(self):
        self.snap("20260910T000000Z", players=0, elements=[1],
                  manifest={"has_players": False})
        self.point_at("20260910T000000Z")
        with self.assertRaises(SystemExit) as cm:
            load_snapshot()
        self.assertIn("--skip-players", str(cm.exception))

    def test_complete_snapshot_loads(self):
        self.snap("20260910T000000Z", players=2,
                  manifest={"has_players": True, "element_count": 2})
        self.point_at("20260910T000000Z")
        snap, bootstrap, fixture_list, players_dir = load_snapshot()
        self.assertEqual(len(bootstrap["elements"]), 2)
        self.assertTrue(players_dir.is_dir())

    def test_no_latest_pointer_refused(self):
        with self.assertRaises(SystemExit):
            load_snapshot()

