"""Execute the repository source-size gate at its exact reviewed boundary.

The gate counts source resources under skills/. Generated Python caches (files inside a
__pycache__ directory, or ending in .pyc or .pyo) are excluded; everything else counts.
"""

import ast
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_LIMIT = 1_200_000
CACHE_FILE_BYTES = 25_000


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

    def check_size(self, size, other_source_bytes=0):
        (self.skill / "payload.txt").write_bytes(b"x" * (size - self.entry_bytes - other_source_bytes))
        return subprocess.run([sys.executable, "-B", str(self.root / "tools" / "selfcheck.py")],
                              capture_output=True, text=True, timeout=15)

    def write_files(self, paths, size):
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"c" * size)
        return len(paths) * size

    def write_generated_caches(self):
        return self.write_files((
            self.root / "skills" / "__pycache__" / "stray.cpython-312.pyc",
            self.skill / "__pycache__" / "kernel.cpython-314.pyc",
            self.skill / "__pycache__" / "kernel.cpython-312.opt-1.pyc",
            self.skill / "__pycache__" / "unrelated.txt",
            self.skill / "scripts" / "__pycache__" / "run.cpython-311.pyc",
            self.skill / "legacy.pyc",
            self.skill / "optimized.pyo",
        ), CACHE_FILE_BYTES)

    def assert_over_limit(self, result, size):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("skills/ source is %d bytes, generated caches excluded (limit %s)"
                      % (size, format(SOURCE_LIMIT, ",")), result.stdout)
        self.assertIn("selfcheck: FAIL", result.stdout)

    def test_exact_aggregate_source_limit_passes(self):
        result = self.check_size(SOURCE_LIMIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("selfcheck: PASS", result.stdout)

    def test_one_byte_over_aggregate_source_limit_fails(self):
        result = self.check_size(SOURCE_LIMIT + 1)
        self.assert_over_limit(result, SOURCE_LIMIT + 1)
        self.assertIn("skills/ source is 1200001 bytes, generated caches excluded (limit 1,200,000)",
                      result.stdout)

    def test_generated_caches_do_not_count_at_exact_source_limit(self):
        cache_bytes = self.write_generated_caches()
        self.assertGreater(cache_bytes, 0)
        result = self.check_size(SOURCE_LIMIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("selfcheck: PASS", result.stdout)

    def test_one_byte_over_source_limit_fails_with_caches_present(self):
        self.write_generated_caches()
        result = self.check_size(SOURCE_LIMIT + 1)
        self.assert_over_limit(result, SOURCE_LIMIT + 1)

    def test_cache_like_names_outside_the_defined_set_still_count(self):
        near_miss_bytes = self.write_files((
            self.skill / "pycache" / "notes.txt",
            self.skill / "__pycache__.md",
            self.skill / "data" / "__pycache__",
            self.skill / "notes.pyc.txt",
            self.skill / "scripts" / "run.py",
        ), 1_000)
        result = self.check_size(SOURCE_LIMIT + 1, near_miss_bytes)
        self.assert_over_limit(result, SOURCE_LIMIT + 1)

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
