"""Tests for the package layout: which stage may import which.

The stages run ingest -> models -> log -> evaluation (fpl/__init__.py). Each
model's command writes through the log's contract, so models import
fpl.log.contract, and fpl.log.entry runs the models. That is safe only while
contract.py imports nothing from fpl and no subpackage's __init__ imports
anything, so neither direction can close an import cycle. These tests keep it
so. Read from the source, so a violation fails here before anything imports.
"""

import ast
import unittest
from pathlib import Path

FPL = Path(__file__).resolve().parent.parent / "fpl"
STAGES = ("ingest", "models", "log", "evaluation")


def fpl_imports(path):
    """Every fpl module a file imports, as dotted names."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module == "fpl" or node.module.startswith("fpl."):
                found.add(node.module)
                found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name.startswith("fpl"))
    return found


def modules(stage):
    return sorted((FPL / stage).glob("*.py"))


class TestLayout(unittest.TestCase):
    def test_ingest_imports_no_later_stage(self):
        for path in modules("ingest"):
            with self.subTest(module=path.name):
                later = {
                    m
                    for m in fpl_imports(path)
                    if m.split(".")[1:2] and m.split(".")[1] in STAGES[1:]
                }
                self.assertEqual(later, set())

    def test_the_log_contract_imports_nothing_from_fpl(self):
        self.assertEqual(fpl_imports(FPL / "log" / "contract.py"), set())

    def test_models_reach_the_log_only_through_its_contract(self):
        for path in modules("models"):
            with self.subTest(module=path.name):
                log = {m for m in fpl_imports(path) if m.startswith("fpl.log")}
                self.assertTrue(all(m.startswith("fpl.log.contract") for m in log), log)

    def test_no_stage_package_init_imports_anything(self):
        """An import in an __init__ is how a two-way dependency becomes a cycle."""
        for stage in STAGES:
            with self.subTest(stage=stage):
                tree = ast.parse((FPL / stage / "__init__.py").read_text())
                imports = [n for n in ast.walk(tree) if isinstance(n, ast.Import | ast.ImportFrom)]
                self.assertEqual(imports, [])

    def test_the_verifier_needs_no_model(self):
        """It checks an entry against its snapshot; it does not run a model to do it."""
        models = {
            m for m in fpl_imports(FPL / "log" / "verify_entry.py") if m.startswith("fpl.models")
        }
        self.assertEqual(models, set())


if __name__ == "__main__":
    unittest.main()
