"""The scheduled ingest (#93): properties of .github/workflows/ingest.yml.

Read as text, as in test_entry_bot.TestWorkflow: there is no YAML parser in the
standard library, and these are properties of the file, not of a parse of it.
"""

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "ingest.yml").read_text()
SCRIPTS = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]


def steps():
    return WORKFLOW.split("steps:", 1)[1]


class TestIngestWorkflow(unittest.TestCase):

    def test_every_command_it_runs_exists(self):
        called = set(re.findall(r"\b(fpl-[a-z]+)\b", steps()))
        self.assertEqual(called, {"fpl-fetch", "fpl-load"})
        for command in called:
            with self.subTest(command=command):
                self.assertIn(command, SCRIPTS)

    def test_it_is_scheduled_and_can_be_run_by_hand(self):
        self.assertRegex(WORKFLOW, r'cron: "[^"]+"')
        self.assertIn("workflow_dispatch:", WORKFLOW)

    def test_it_writes_nothing_to_the_repository(self):
        """Snapshots stay out of git (rule 3)."""
        permissions = WORKFLOW.split("permissions:", 1)[1].split("\n\n", 1)[0]
        self.assertIn("contents: read", permissions)
        self.assertNotIn("write", permissions)
        self.assertNotIn("git push", WORKFLOW)

    def test_the_url_is_checked_before_the_slow_fetch(self):
        body = steps()
        self.assertLess(body.index("tools/check_database_url.py"), body.index("fpl-fetch"))
        self.assertLess(body.index("fpl-fetch"), body.index("fpl-load"))

    def test_the_database_url_reaches_only_the_steps_that_need_it(self):
        """Not the fetch, which talks to the FPL API and nothing else."""
        fetch = steps().split("Snapshot the FPL API", 1)[1].split("- name:", 1)[0]
        self.assertNotIn("DATABASE_URL", fetch)
        self.assertIn("environment: production", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
