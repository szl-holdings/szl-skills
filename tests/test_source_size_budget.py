"""Execute the repository source-size gate at its exact reviewed boundary."""

import ast
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_LIMIT = 1_083_000


class SourceSizeBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="szl-source-budget-")
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        tools = self.root / "tools"
        tools.mkdir()
        for name in ("selfcheck.py", "skill_inventory.py"):
            shutil.copyfile(ROOT / "tools" / name, tools / name)
        (self.root / "LICENSE").write_text("Synthetic gate fixture.\n", encoding="utf-8")
        (tools / "install_claude_science.py").write_text(
            'NAMES = ["szl-budget-fixture"]\n', encoding="utf-8")
        self.skill = self.root / "skills" / "szl-budget-fixture"
        self.skill.mkdir(parents=True)
        entry = self.skill / "SKILL.md"
        entry.write_text(
            "---\nname: szl-budget-fixture\ndescription: Test source volume only.\n---\n"
            "Do not interpret this fixture as an application import or science result.\n",
            encoding="utf-8",
        )
        self.entry_bytes = entry.stat().st_size
        market = self.root / ".claude-plugin"
        market.mkdir()
        (market / "marketplace.json").write_text(json.dumps({"plugins": [
            {"name": "szl-science-skills", "skills": ["./skills/szl-budget-fixture"]},
            {"name": "szl-evidence-skills", "skills": []},
        ]}), encoding="utf-8")
        subprocess.run([sys.executable, "-B", str(tools / "skill_inventory.py"), "--write"],
                       check=True, capture_output=True, text=True, timeout=15)

    def check_size(self, size):
        (self.skill / "payload.txt").write_bytes(b"x" * (size - self.entry_bytes))
        return subprocess.run([sys.executable, "-B", str(self.root / "tools" / "selfcheck.py")],
                              capture_output=True, text=True, timeout=15)

    def test_exact_aggregate_source_limit_passes(self):
        result = self.check_size(SOURCE_LIMIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("selfcheck: PASS", result.stdout)

    def test_one_byte_over_aggregate_source_limit_fails(self):
        result = self.check_size(SOURCE_LIMIT + 1)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("skills/ is 1083001 bytes (limit 1,083,000)", result.stdout)
        self.assertIn("selfcheck: FAIL", result.stdout)

    def test_ignored_python_cache_is_not_source_volume(self):
        # Importing a skill module writes __pycache__ beside it. The packager,
        # installer and inventory skip those ignored caches; the source budget must too.
        cache = self.skill / "__pycache__"
        cache.mkdir()
        (cache / "kernel.cpython-313.pyc").write_bytes(b"\0" * 4096)
        result = self.check_size(SOURCE_LIMIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("selfcheck: PASS", result.stdout)

    def test_selfcheck_without_b_writes_no_bytecode_into_skills(self):
        # CI runs `python tools/selfcheck.py` without -B, and its behavioral tests
        # load skill modules by path. A second run must see the same source bytes.
        scripts = self.skill / "scripts"
        scripts.mkdir()
        (scripts / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "tools" / "test_fixture_import.py").write_text(textwrap.dedent('''\
            import importlib.util
            import pathlib
            import unittest

            HELPER = (pathlib.Path(__file__).resolve().parents[1]
                      / "skills" / "szl-budget-fixture" / "scripts" / "helper.py")


            class FixtureImportTests(unittest.TestCase):
                def test_fixture_skill_module_loads(self):
                    spec = importlib.util.spec_from_file_location("budget_fixture_helper", HELPER)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    self.assertEqual(module.VALUE, 1)
            '''), encoding="utf-8")
        environment = os.environ.copy()
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
        environment.pop("PYTHONPYCACHEPREFIX", None)
        for _ in range(2):
            result = subprocess.run([sys.executable, str(self.root / "tools" / "selfcheck.py")],
                                    capture_output=True, text=True, timeout=30, env=environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("test_fixture_skill_module_loads", result.stderr)
        self.assertEqual(sorted((self.root / "skills").rglob("*.pyc")), [])

    def test_installer_skill_and_batch_limits_are_unchanged(self):
        parsed = ast.parse((ROOT / "tools" / "install_claude_science.py").read_text(encoding="utf-8"))
        for name, expected in (("MAX_SKILL_BYTES", 200_000),
                               ("MAX_BATCH_BYTES", 1_000_000), ("MAX_BATCHES", 8)):
            assignments = [node.value for node in parsed.body if isinstance(node, ast.Assign)
                           and any(isinstance(target, ast.Name) and target.id == name
                                   for target in node.targets)]
            self.assertEqual(len(assignments), 1)
            actual = ast.literal_eval(assignments[0])
            self.assertIs(type(actual), int)
            self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
