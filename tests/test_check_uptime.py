"""tools/check_uptime.py: what it fails on, and what it passes (#86)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import check_uptime  # noqa: E402

FRESH = {
    "data_age_seconds": 3 * 3600,
    "latest_settled_gameweek": 8,
    "next_gameweek": 9,
    "next_gameweek_entries": [],
}


class TestCheck(unittest.TestCase):
    def test_up_and_fresh_passes(self):
        ok, lines = check_uptime.check((200, {}), (200, FRESH), 8)
        self.assertTrue(ok, lines)

    def test_no_answer_from_healthz_is_down(self):
        ok, lines = check_uptime.check((0, None), (0, None), 8)
        self.assertFalse(ok)
        self.assertIn("down", lines[-1])

    def test_a_status_that_fails_is_the_database(self):
        ok, lines = check_uptime.check((200, {}), (503, None), 8)
        self.assertFalse(ok)
        self.assertIn("database", lines[-1])

    def test_data_older_than_the_limit_is_stale(self):
        old = FRESH | {"data_age_seconds": (check_uptime.STALE_HOURS + 1) * 3600}
        ok, lines = check_uptime.check((200, {}), (200, old), 8)
        self.assertFalse(ok)
        self.assertTrue(any("STALE" in line for line in lines))

    def test_behind_fpl_for_more_than_a_day_fails(self):
        day_old = FRESH | {"data_age_seconds": 25 * 3600}
        ok, lines = check_uptime.check((200, {}), (200, day_old), 9)
        self.assertFalse(ok)
        self.assertTrue(any("BEHIND" in line for line in lines))

    def test_behind_fpl_by_hours_is_the_ingest_still_to_run(self):
        ok, _ = check_uptime.check((200, {}), (200, FRESH), 9)
        self.assertTrue(ok)

    def test_fpl_unreadable_checks_age_only(self):
        ok, _ = check_uptime.check((200, {}), (200, FRESH), None)
        self.assertTrue(ok)

    def test_settled_by_fpl_is_finished_and_data_checked(self):
        events = [
            {"id": 7, "finished": True, "data_checked": True},
            {"id": 8, "finished": True, "data_checked": False},
        ]
        self.assertEqual(check_uptime.settled_by_fpl({"events": events}), 7)


if __name__ == "__main__":
    unittest.main()
