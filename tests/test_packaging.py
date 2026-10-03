"""Tests for pyproject.toml: the package declaration and its console commands.

Read from the file rather than from an installed environment, so a broken
declaration fails here even when nothing has been installed — and a command
nobody declared cannot be missed just because an old install still has it.

Run from the repository root: python -m unittest discover tests
"""

import ast
import contextlib
import importlib
import io
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

with (ROOT / "pyproject.toml").open("rb") as f:
    PYPROJECT = tomllib.load(f)

SCRIPTS = PYPROJECT["project"]["scripts"]


def has_main_block(path):
    """Whether a module ends in `if __name__ == "__main__":`, i.e. is a command."""
    for node in ast.parse(path.read_text()).body:
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"):
            return True
    return False


class TestScripts(unittest.TestCase):

    def test_every_script_resolves_to_a_callable(self):
        for command, target in SCRIPTS.items():
            with self.subTest(command=command):
                module, _, attr = target.partition(":")
                self.assertTrue(callable(getattr(importlib.import_module(module), attr)))

    def test_every_command_module_has_a_script(self):
        """A new `python -m fpl.x` command cannot ship without its console script."""
        declared = {target.partition(":")[0] for target in SCRIPTS.values()}
        commands = {f"fpl.{p.stem}" for p in (ROOT / "fpl").glob("*.py") if has_main_block(p)}
        self.assertEqual(commands, declared)

    def test_every_script_is_the_modules_cli(self):
        """`fpl-x` and `python -m fpl.x` both run cli(), so they cannot drift apart."""
        for command, target in SCRIPTS.items():
            with self.subTest(command=command):
                self.assertEqual(target.partition(":")[2], "cli")

    def test_help_exits_cleanly(self):
        for command, target in SCRIPTS.items():
            with self.subTest(command=command):
                module, _, attr = target.partition(":")
                cli = getattr(importlib.import_module(module), attr)
                with self.assertRaises(SystemExit) as raised, \
                        contextlib.redirect_stdout(io.StringIO()):
                    cli(["--help"])
                self.assertEqual(raised.exception.code, 0)


class TestPackage(unittest.TestCase):

    def test_only_fpl_ships(self):
        """tests/, tools/, data/ and scratch/ must never end up installed."""
        self.assertEqual(PYPROJECT["tool"]["setuptools"]["packages"], ["fpl"])

    def test_one_python(self):
        self.assertEqual(PYPROJECT["project"]["requires-python"], "==3.13.*")


if __name__ == "__main__":
    unittest.main()
