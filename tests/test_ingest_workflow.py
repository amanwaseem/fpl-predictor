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


def step(name):
    return steps().split(f"name: {name}", 1)[1].split("- name:", 1)[0]


class TestIngestWorkflow(unittest.TestCase):

    def test_every_command_it_runs_exists(self):
        called = set(re.findall(r"\b(fpl-[a-z]+)\b", steps()))
        self.assertEqual(called, {"fpl-fetch", "fpl-stale", "fpl-load"})
        for command in called:
            with self.subTest(command=command):
                self.assertIn(command, SCRIPTS)

    def test_it_is_scheduled_and_can_be_run_by_hand(self):
        self.assertRegex(WORKFLOW, r'cron: "[^"]+"')
        self.assertIn("workflow_dispatch:", WORKFLOW)

    def test_a_failure_inside_a_pipe_fails_its_step(self):
        """GitHub's default shell has no pipefail; `shell: bash` adds it."""
        self.assertRegex(WORKFLOW, r"defaults:\s+run:\s+shell: bash")

    def test_it_writes_nothing_to_the_repository(self):
        """Snapshots stay out of git (rule 3)."""
        permissions = WORKFLOW.split("permissions:", 1)[1].split("\n\n", 1)[0]
        self.assertIn("contents: read", permissions)
        self.assertNotIn("write", permissions)
        self.assertNotIn("git push", WORKFLOW)

    def test_the_order_of_the_steps(self):
        body = steps()
        order = ["tools/check_database_url.py", "fpl-fetch --skip-players",
                 "fpl-stale |", "run: fpl-fetch\n", "--workflow deploy.yml",
                 "--schema-only", "fpl-load"]
        positions = [body.index(marker) for marker in order]
        self.assertEqual(positions, sorted(positions))

    def test_the_full_fetch_and_load_only_run_when_needed(self):
        for name in ("Snapshot the FPL API in full", "Wait for any deploy in progress",
                     "Load the snapshot into production"):
            with self.subTest(step=name):
                self.assertIn("steps.stale.outputs.needed == 'true'", step(name))

    def test_the_database_url_reaches_only_the_steps_that_need_it(self):
        """Not the fetches, which talk to the FPL API and nothing else."""
        before_jobs, jobs = WORKFLOW.split("\njobs:", 1)
        job_header = jobs.split("steps:", 1)[0]
        self.assertNotIn("secrets.DATABASE_URL", before_jobs)
        self.assertNotIn("secrets.DATABASE_URL", job_header)
        for name in ("Snapshot the bootstrap and fixtures", "Snapshot the FPL API in full",
                     "Wait for any deploy in progress"):
            with self.subTest(step=name):
                self.assertNotIn("DATABASE_URL", step(name))
        self.assertIn("environment: production", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
