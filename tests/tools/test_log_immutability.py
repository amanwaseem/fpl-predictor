"""Tests for the mechanical enforcement of hard rule 1.

`fpl/log/contract.py` stops a second *write* through write_entry. It does nothing about
an editor, a `sed -i`, or a rebase — the ways a committed prediction actually
gets altered. `tools/check_log_immutable.py` is what catches those, so these
tests exercise it against a real git repository rather than a mock: the tool's
entire job is reading git, and a mocked git would test the mock.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "check_log_immutable.py"

ENTRY = "gameweek,player_id,predicted_points\n4,1,6.30\n"


class FixtureRepo(unittest.TestCase):
    """A throwaway git repo with one committed prediction entry.

    Global and system git config are pointed at os.devnull so that whatever
    the developer has set — a signing key, a default branch name, a hook
    template — cannot change what these tests exercise.
    """

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.write("predictions/gw04_baseline-v1.csv", ENTRY)
        self.write("scores/gw04_baseline-v1.json", '{"mae": 2.1}\n')
        self.git("add", "-A")
        self.git("commit", "-qm", "Commit the GW4 entry")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def git(self, *args):
        subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, check=True, stdout=subprocess.DEVNULL
        )

    def write(self, relpath, text):
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def check(self, ref="HEAD", *args):
        return subprocess.run(
            [sys.executable, str(TOOL), "--ref", ref, *args],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
        )


class TestImmutabilityChecker(FixtureRepo):
    def test_untouched_tree_passes(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unchanged", result.stdout)

    def test_modified_entry_fails_and_names_the_path(self):
        self.write(
            "predictions/gw04_baseline-v1.csv", "gameweek,player_id,predicted_points\n4,1,9.99\n"
        )
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("predictions/gw04_baseline-v1.csv", result.stderr)
        self.assertIn("modified", result.stderr)

    def test_deleted_entry_fails(self):
        (self.repo / "predictions/gw04_baseline-v1.csv").unlink()
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("deleted", result.stderr)

    def test_new_entry_passes(self):
        """Appending is the whole point. A new gameweek is not a violation."""
        self.write("predictions/gw05_baseline-v1.csv", ENTRY)
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gw05_baseline-v1.csv", result.stdout)

    def test_a_new_entry_alongside_a_modified_one_still_fails(self):
        """Adding a legitimate entry must not launder an edit to an old one."""
        self.write("predictions/gw05_baseline-v1.csv", ENTRY)
        self.write("predictions/gw04_baseline-v1.csv", "tampered\n")
        self.assertEqual(self.check().returncode, 1)

    def test_whitespace_only_change_still_fails(self):
        """Byte comparison, not a semantic diff — 'it only reordered' is
        exactly the argument rule 1 exists to refuse."""
        self.write("predictions/gw04_baseline-v1.csv", ENTRY + "\n")
        self.assertEqual(self.check().returncode, 1)

    def test_scores_are_not_covered(self):
        """scores/ is derived output, regenerable, and meant to be rewritten
        when a metric definition changes. Freezing it would freeze the metrics
        instead of the evidence."""
        self.write("scores/gw04_baseline-v1.json", '{"mae": 1.7}\n')
        self.assertEqual(self.check().returncode, 0)

    def test_unresolvable_ref_fails_closed(self):
        """An unverifiable log is not a verified one."""
        result = self.check(ref="origin/nonexistent")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot resolve", result.stderr)

    def test_reports_when_nothing_is_committed_yet(self):
        empty = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q"], cwd=empty, env=self.env, check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@e.com"], cwd=empty, env=self.env, check=True
        )
        subprocess.run(["git", "config", "user.name", "T"], cwd=empty, env=self.env, check=True)
        (empty / "README.md").write_text("x\n")
        subprocess.run(["git", "add", "-A"], cwd=empty, env=self.env, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=empty, env=self.env, check=True)
        result = subprocess.run(
            [sys.executable, str(TOOL), "--ref", "HEAD"],
            cwd=empty,
            env=self.env,
            capture_output=True,
            text=True,
        )
        shutil.rmtree(empty, ignore_errors=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing to protect yet", result.stdout)


def in_minutes(minutes):
    return (datetime.now(UTC) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def entry_due(deadline):
    return f"gameweek,player_id,predicted_points,deadline_utc\n5,1,6.30,{deadline}\n"


class TestNewEntriesBeforeTheirDeadline(FixtureRepo):
    """Entry PRs merge themselves once ci is green, so ci refuses a late one (#119)."""

    def check_on_time(self):
        return self.check("HEAD", "--on-time")

    def test_a_new_entry_well_before_its_deadline_passes(self):
        self.write("predictions/gw05_baseline-v1.csv", entry_due("2999-01-01T11:00:00Z"))
        result = self.check_on_time()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_new_entry_past_its_deadline_fails_and_names_it(self):
        self.write("predictions/gw05_baseline-v1.csv", entry_due("2001-01-01T11:00:00Z"))
        result = self.check_on_time()
        self.assertEqual(result.returncode, 1)
        self.assertIn("gw05_baseline-v1.csv", result.stderr)
        self.assertIn("has passed", result.stderr)

    def test_a_new_entry_inside_the_margin_fails_without_calling_it_missed(self):
        self.write("predictions/gw05_baseline-v1.csv", entry_due(in_minutes(2)))
        result = self.check_on_time()
        self.assertEqual(result.returncode, 1)
        self.assertIn("too close to merge", result.stderr)
        self.assertNotIn("Z, has passed", result.stderr)

    def test_a_hand_entry_a_quarter_of_an_hour_out_passes(self):
        """The runbook's fallback can open an entry PR this late."""
        self.write("predictions/gw05_baseline-v1.csv", entry_due(in_minutes(15)))
        self.assertEqual(self.check_on_time().returncode, 0)

    def test_every_row_must_carry_the_same_deadline(self):
        mixed = entry_due("2999-01-01T11:00:00Z") + "5,2,1.00,2001-01-01T11:00:00Z\n"
        self.write("predictions/gw05_baseline-v1.csv", mixed)
        result = self.check_on_time()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no single readable deadline_utc", result.stderr)

    def test_an_entry_that_is_not_text_fails_closed_without_a_traceback(self):
        path = self.repo / "predictions/gw05_baseline-v1.csv"
        path.write_bytes(b"\xff\xfe\x00garbage")
        result = self.check_on_time()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_new_entry_without_a_deadline_fails_closed(self):
        self.write("predictions/gw05_baseline-v1.csv", ENTRY)
        result = self.check_on_time()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no single readable deadline_utc", result.stderr)

    def test_committed_entries_are_not_judged_by_the_clock(self):
        """GW4's deadline is long past; only what the PR adds is checked."""
        self.write("predictions/gw04_baseline-v1.csv", entry_due("2001-01-01T11:00:00Z"))
        self.git("add", "-A")
        self.git("commit", "-qm", "An old entry")
        self.assertEqual(self.check_on_time().returncode, 0)

    def test_without_the_flag_the_clock_is_not_read(self):
        self.write("predictions/gw05_baseline-v1.csv", entry_due("2001-01-01T11:00:00Z"))
        self.assertEqual(self.check().returncode, 0)


class TestEverySeason(FixtureRepo):
    """#72: a later season's directory is the log too."""

    def setUp(self):
        super().setUp()
        self.write("predictions-2027-28/gw04_baseline-v1.csv", ENTRY)
        self.git("add", "-A")
        self.git("commit", "-qm", "Commit 2027/28's GW4 entry")

    def test_a_later_seasons_entry_is_protected(self):
        self.write("predictions-2027-28/gw04_baseline-v1.csv", ENTRY.replace("6.30", "9.99"))
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("predictions-2027-28/gw04_baseline-v1.csv: modified", result.stderr)

    def test_both_seasons_gw4_are_counted(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 committed entries unchanged", result.stdout)

    def test_a_directory_merely_named_like_the_log_is_not_it(self):
        self.write("predictions-old/gw04_baseline-v1.csv", ENTRY)
        self.git("add", "-A")
        self.git("commit", "-qm", "Not the log")
        self.write("predictions-old/gw04_baseline-v1.csv", "edited\n")
        self.assertEqual(self.check().returncode, 0)

    def test_the_tools_pattern_matches_the_contracts_naming(self):
        check_log_immutable = tool()
        from fpl.log.contract import season_directory, season_of_directory

        for season in ("2026/27", "2027/28", "2040/41"):
            name = str(season_directory(season))
            self.assertTrue(check_log_immutable.LOG_DIRS.match(name), name)
        # The tool may protect more than the contract names, never less: a
        # malformed season name is matched (fails closed), other names are not.
        for name in ("predictions-old", "predictions-2027", "scores-2027-28"):
            self.assertIsNone(check_log_immutable.LOG_DIRS.match(name), name)
            self.assertIsNone(season_of_directory(name), name)


def tool():
    """The tool as a module, imported without leaving tools/ on sys.path."""
    sys.path.insert(0, str(TOOL.parent))
    try:
        import check_log_immutable
    finally:
        sys.path.remove(str(TOOL.parent))
    return check_log_immutable


LATE = "2001-01-01T11:00:00Z"


class TestAcceptedLateMerges(FixtureRepo):
    """A late entry passes only as accepted on the base commit (ADR 0026)."""

    path = "predictions/gw09_baseline-v1.csv"

    def record(self, text, **changes):
        return {
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "pr": 117,
            "pr_opened_utc": "2001-01-01T01:00:00Z",
            "deadline_utc": LATE,
        } | changes

    def accept(self, **changes):
        """Commit an acceptance for the entry, as its own merged PR would."""
        text = entry_due(LATE)
        self.write("tools/late_merges.json", json.dumps({self.path: self.record(text, **changes)}))
        self.git("add", "-A")
        self.git("commit", "-qm", "Accept a late merge")
        self.write(self.path, text)

    def on_time(self):
        return self.check("HEAD", "--on-time")

    def test_an_entry_accepted_on_the_base_commit_passes(self):
        self.accept()
        result = self.on_time()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("accepted late merge", result.stdout)

    def test_a_pr_cannot_accept_its_own_late_entry(self):
        text = entry_due(LATE)
        self.write(self.path, text)
        self.write("tools/late_merges.json", json.dumps({self.path: self.record(text)}))
        self.assertEqual(self.on_time().returncode, 1)

    def test_an_entry_changed_since_it_was_accepted_is_late(self):
        self.accept()
        self.write(self.path, entry_due(LATE) + "\n")
        result = self.on_time()
        self.assertEqual(result.returncode, 1)
        self.assertIn("changed since it was accepted", result.stderr)

    def test_a_record_whose_pr_opened_after_the_deadline_accepts_nothing(self):
        self.accept(pr_opened_utc="2001-01-01T12:00:00Z")
        self.assertIn("opened after the deadline", self.on_time().stderr)

    def test_a_record_for_another_deadline_accepts_nothing(self):
        self.accept(deadline_utc="2999-01-01T11:00:00Z")
        self.assertIn("deadline_utc is not the entry's", self.on_time().stderr)

    def test_an_accepted_record_cannot_be_changed_or_removed(self):
        self.accept()
        self.write("tools/late_merges.json", "{}")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("changed or removed", result.stderr)

    def test_a_malformed_list_on_the_base_commit_says_so(self):
        self.write("tools/late_merges.json", "{,}")
        self.git("add", "-A")
        self.git("commit", "-qm", "A typo")
        self.write(self.path, entry_due(LATE))
        result = self.on_time()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not valid JSON", result.stderr)


class TestTheCommittedList(unittest.TestCase):
    def test_each_record_is_for_a_log_entry_whose_pr_opened_before_its_deadline(self):
        records = json.loads((ROOT / tool().LATE_MERGES).read_text())
        for path, record in records.items():
            if path.startswith("_"):
                continue
            with self.subTest(path=path):
                self.assertRegex(path, r"^predictions(-\d{4}-\d{2})?/gw\d{2}_[^_/]+\.csv$")
                self.assertRegex(record["sha256"], r"^[0-9a-f]{64}$")
                self.assertLess(record["pr_opened_utc"], record["deadline_utc"])
                # Before its entry is merged, the record is all there is to check.
                if (ROOT / path).exists():
                    self.assertIsNone(tool().accepted_late(path, ROOT, records))
