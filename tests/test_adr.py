"""Tests for docs/adr/: the decision records stay numbered, valid and indexed.

Run from the repository root: python -m unittest discover tests
"""

import re
import unittest
from pathlib import Path

ADR_DIR = Path(__file__).resolve().parent.parent / "docs" / "adr"
NAME = re.compile(r"^(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
STATUS = re.compile(r"^- \*\*Status:\*\* (Accepted|Proposed|Open|Superseded by (\d{4}))$", re.M)


def records(directory=ADR_DIR):
    """{number: path} for every record, failing on a misnamed file."""
    found = {}
    for path in sorted(directory.glob("*.md")):
        if path.name == "README.md":
            continue
        match = NAME.match(path.name)
        if not match:
            raise AssertionError(f"{path.name} is not named NNNN-kebab-title.md")
        number = int(match.group(1))
        if number in found:
            raise AssertionError(f"{number:04d} is used twice: {found[number].name}, {path.name}")
        found[number] = path
    return found


def problems(directory=ADR_DIR):
    """Every way the record set is broken, as readable strings."""
    try:
        found = records(directory)
    except AssertionError as e:
        return [str(e)]
    faults = []
    if sorted(found) != list(range(1, len(found) + 1)):
        faults.append(f"numbering is not 0001..{len(found):04d} without gaps: {sorted(found)}")
    index = (directory / "README.md").read_text()
    for number, path in found.items():
        text = path.read_text()
        if not text.startswith(f"# {number:04d}. "):
            faults.append(f"{path.name}: title does not start with '# {number:04d}. '")
        status = STATUS.search(text)
        if not status:
            faults.append(f"{path.name}: no valid Status line")
        elif status.group(2) and int(status.group(2)) not in found:
            faults.append(f"{path.name}: superseded by {status.group(2)}, which does not exist")
        for section in ("## Context", "## Decision", "## Consequences", "## Revisit if"):
            if section not in text:
                faults.append(f"{path.name}: missing '{section}'")
        if f"]({path.name})" not in index:
            faults.append(f"{path.name}: not linked from docs/adr/README.md")
    return faults


class TestRecords(unittest.TestCase):

    def test_the_record_set_is_sound(self):
        self.assertEqual(problems(), [])

    def test_there_is_at_least_the_first_record(self):
        self.assertIn(1, records())


if __name__ == "__main__":
    unittest.main()
