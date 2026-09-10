"""Tests for live-results ingestion and reading it back.

No network. `fetch.get` is replaced with a stub that records every path it was
asked for, which is what makes "one request per gameweek, not per player" and
"resume does not refetch" testable claims rather than intentions.

Run from the repository root: python -m unittest discover tests
"""

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tests import fixtures
from fpl import fetch
from fpl.snapshot import load_live


def live_payload(gw, points=6):
    """The shape of event/<gw>/live/: one entry per element, with stats."""
    return {"elements": [{"id": pid, "stats": {"total_points": points,
                                               "minutes": 90}}
                         for pid in (1, 2)]}


class StubbedAPI(fixtures.TempCwd):
    """A fetch whose every request is served from canned payloads."""

    def setUp(self):
        super().setUp()
        self.requested = []
        patcher = patch.object(fetch, "get", side_effect=self.serve)
        self.get = patcher.start()
        self.addCleanup(patcher.stop)
        # The politeness delay is real time; nothing here is polite to.
        sleep = patch.object(fetch.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        # fetch narrates its progress, which is right at a terminal and noise
        # in a suite. Captured rather than silenced so a failure can show it.
        self.output = io.StringIO()
        quiet = redirect_stdout(self.output)
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def serve(self, path):
        self.requested.append(path)
        if path == "bootstrap-static/":
            return self.bootstrap()
        if path == "fixtures/":
            return []
        if path.startswith("event/"):
            return live_payload(int(path.split("/")[1]))
        if path.startswith("element-summary/"):
            return {"history": []}
        raise AssertionError(f"unexpected request: {path}")

    def bootstrap(self, settled_through=3):
        return {
            "elements": [fixtures.element(1), fixtures.element(2)],
            "teams": [fixtures.team(1)],
            "element_types": fixtures.ELEMENT_TYPES,
            "events": fixtures.season(settled_through + 1,
                                      "2026-09-12T12:30:00Z"),
        }

    def live_requests(self):
        return [p for p in self.requested if p.startswith("event/")]

    def out(self, name="20260910T000000Z"):
        d = self.tmp / "data/raw" / name
        d.mkdir(parents=True, exist_ok=True)
        return d


class TestFetchLive(StubbedAPI):
    def test_written_for_settled_gameweeks(self):
        outdir = self.out()
        events = self.bootstrap()["events"]
        written = fetch.fetch_live(outdir, events)
        self.assertEqual(written, [1, 2, 3])
        for gw in (1, 2, 3):
            self.assertTrue((outdir / "live" / f"{gw}.json").is_file())

    def test_unsettled_gameweeks_skipped(self):
        """data_checked is false until bonus is settled, so points still move."""
        outdir = self.out()
        events = self.bootstrap()["events"]  # GW4 is is_next, not data_checked
        fetch.fetch_live(outdir, events)
        self.assertFalse((outdir / "live" / "4.json").exists())
        self.assertNotIn("event/4/live/", self.live_requests())

    def test_one_request_per_gameweek(self):
        """The point of storing this: scoring never refetches 654 players."""
        fetch.fetch_live(self.out(), self.bootstrap()["events"])
        self.assertEqual(self.live_requests(),
                         ["event/1/live/", "event/2/live/", "event/3/live/"])

    def test_nothing_settled_yet(self):
        outdir = self.out()
        events = fixtures.season(1, "2026-08-14T17:30:00Z")
        self.assertEqual(fetch.fetch_live(outdir, events), [])
        self.assertEqual(self.live_requests(), [])

    def test_existing_file_is_not_refetched(self):
        outdir = self.out()
        events = self.bootstrap()["events"]
        fetch.fetch_live(outdir, events)
        first = list(self.live_requests())

        fetch.fetch_live(outdir, events)
        self.assertEqual(self.live_requests(), first,
                         "a second run refetched live data already on disk")

    def test_resumed_file_content_is_left_alone(self):
        outdir = self.out()
        (outdir / "live").mkdir(parents=True)
        (outdir / "live" / "1.json").write_text(json.dumps({"elements": "mine"}))
        fetch.fetch_live(outdir, self.bootstrap()["events"])
        kept = json.loads((outdir / "live" / "1.json").read_text())
        self.assertEqual(kept["elements"], "mine")


class TestManifest(StubbedAPI):
    def manifest(self):
        snap = self.tmp / "data/raw" / (self.tmp / "data/raw/LATEST").read_text()
        return json.loads((snap / "manifest.json").read_text())

    def test_records_which_gameweeks_have_live_data(self):
        fetch.main(skip_players=False, resume=False)
        self.assertEqual(self.manifest()["live_gameweeks"], [1, 2, 3])

    def test_snapshot_stays_self_describing_with_none(self):
        with patch.object(StubbedAPI, "bootstrap",
                          lambda self, settled_through=0: {
                              "elements": [fixtures.element(1)],
                              "teams": [fixtures.team(1)],
                              "element_types": fixtures.ELEMENT_TYPES,
                              "events": fixtures.season(
                                  1, "2026-08-14T17:30:00Z")}):
            fetch.main(skip_players=False, resume=False)
        self.assertEqual(self.manifest()["live_gameweeks"], [])

    def test_bootstrap_only_snapshot_still_gets_live(self):
        """Live data is orthogonal to player history and costs three requests."""
        fetch.main(skip_players=True, resume=False)
        snap = next((self.tmp / "data/raw").glob("2026*"))
        self.assertEqual(json.loads((snap / "manifest.json").read_text())
                         ["live_gameweeks"], [1, 2, 3])


class TestLoadLive(StubbedAPI):
    def test_reads_back_what_was_written(self):
        outdir = self.out()
        fetch.fetch_live(outdir, self.bootstrap()["events"])
        payload = load_live(outdir, 2)
        self.assertEqual([e["id"] for e in payload["elements"]], [1, 2])

    def test_absent_gameweek_refused_rather_than_empty(self):
        """An empty result reads as 'nobody scored', not 'nothing was fetched'."""
        outdir = self.out()
        with self.assertRaises(SystemExit) as cm:
            load_live(outdir, 9)
        self.assertIn("no live results for GW9", str(cm.exception))

    def test_unexpected_shape_refused(self):
        outdir = self.out()
        (outdir / "live").mkdir(parents=True)
        (outdir / "live" / "1.json").write_text(json.dumps({"nope": []}))
        with self.assertRaises(SystemExit) as cm:
            load_live(outdir, 1)
        self.assertIn("API shape has changed", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
