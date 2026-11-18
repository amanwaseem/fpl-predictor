"""Every action a workflow uses is pinned by commit, with its version beside it (#180).

A tag can be moved to other code, and the entry and score workflows hold
write tokens. The version is a comment on the same line, which is where
Dependabot updates it along with the commit.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)(.*)$")
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
VERSION_COMMENT = re.compile(r"^\s+#\s*v?\d[\w.-]*\s*$")


def uses_lines():
    github = REPO_ROOT / ".github"
    files = [*github.glob("workflows/*.yml"), *github.glob("actions/*/action.yml")]
    for path in sorted(files):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            match = USES.match(line)
            if match:
                yield f"{path.relative_to(REPO_ROOT)}:{number}", match.group(1), match.group(2)


class TestActionsArePinned(unittest.TestCase):
    def test_there_are_actions_to_check(self):
        self.assertGreater(len(list(uses_lines())), 10)

    def test_every_action_is_pinned_by_commit_with_its_version(self):
        for where, action, rest in uses_lines():
            if action.startswith("./"):  # this repository's own, at this commit
                continue
            with self.subTest(where=where):
                self.assertRegex(action, PINNED, "pin by the full commit SHA")
                self.assertRegex(rest, VERSION_COMMENT, "put the version after it: # v1.2.3")
