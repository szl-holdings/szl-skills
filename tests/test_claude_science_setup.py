"""Documented SDK contract tests only. This test double is NOT the real application."""
import pathlib
import copy
import json
import runpy
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SETUP = runpy.run_path(str(ROOT / "tools" / "install_claude_science.py"))


class SkillsDouble:
    def __init__(self):
        self.files, self.origins, self.published = {}, {}, []
        self.reject_gate = False

    def list(self):
        return [{"name": n, "origin": origin} for n, origin in self.origins.items()]

    def read(self, name, path="SKILL.md"):
        if (name, path) not in self.files:
            raise FileNotFoundError("file not found")
        return {"content": self.files[name, path]}

    def edit(self, name, path, content, old_string=None):
        previous = self.files.get((name, path))
        if old_string is None and previous is not None:
            raise ValueError("file exists")
        if old_string is not None and (previous is None or previous.count(old_string) != 1):
            raise ValueError("single exact match required")
        self.files[name, path] = content if old_string is None else previous.replace(old_string, content, 1)
        self.origins[name] = "draft"
        return {"action": "edit", "sidecar_gate": {"ok": not self.reject_gate}} if path == "kernel.py" else {"action": "edit"}

    def publish(self, name, overwrite=False):
        self.origins[name] = "personal"
        self.published.append(name)
        return {"status": "published", "name": name}


class AgentsDouble:
    def __init__(self):
        self.profiles = {}

    def list(self):
        return list(self.profiles.values())

    def create(self, name, display_name, description, system_prompt="", skill_names=None):
        self.profiles[name] = {"name": name, "displayName": display_name, "systemPrompt": system_prompt,
                               "skillNames": list(skill_names), "connectors": [], "unrestricted": False}

    def attach_skill(self, name, skill):
        self.profiles[name]["skillNames"].append(skill)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.temp.name) / "receipt.json"
        self.host = types.SimpleNamespace(skills=SkillsDouble(), agents=AgentsDouble())
        self.resources = SETUP["bundle_batches"](ROOT)

    def tearDown(self):
        self.temp.cleanup()

    def test_supported_calls_publish_readback_and_curated_profile(self):
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        science = next(p for p in market["plugins"] if p["name"] == "szl-science-skills")
        evidence = next(p for p in market["plugins"] if p["name"] == "szl-evidence-skills")
        expected = {pathlib.PurePosixPath(p).name for p in science["skills"]}
        self.assertEqual({name for batch in self.resources for name in batch}, expected)
        self.assertEqual(len(SETUP["NAMES"]), len(expected))
        self.assertTrue(expected.isdisjoint(pathlib.PurePosixPath(p).name for p in evidence["skills"]))
        result = SETUP["install"](self.host, self.resources, self.path)
        self.assertEqual(result["status"], "PUBLISHED_AND_READ_BACK")
        self.assertEqual(len(result["skills"]), len(SETUP["NAMES"]))
        self.assertEqual(result["agent"]["connectors"], [])
        self.assertFalse(result["agent"]["unrestricted"])
        self.assertEqual(result["runtime_task_evaluation"], "NOT_EXECUTED")
        self.assertEqual(len(result["staging"]["batches"]), len(self.resources))
        self.assertTrue(all(batch["resource_bytes"] <= 1000000 for batch in result["staging"]["batches"]))
        self.assertEqual(result["staging"]["total_resource_bytes"], sum(
            len(text.encode("utf-8")) for batch in self.resources for files in batch.values() for text in files.values()))
        second = SETUP["install"](self.host, self.resources, self.path.with_name("second.json"))
        self.assertEqual(second["status"], "PUBLISHED_AND_READ_BACK")
        self.assertEqual(len(self.host.skills.published), len(SETUP["NAMES"]))

    def test_collision_preflight_does_not_modify_other_resources(self):
        name = SETUP["NAMES"][-1]
        self.assertGreater(len(self.resources), 1)
        self.assertIn(name, self.resources[-1])
        self.host.skills.origins[name] = "personal"
        self.host.skills.files[name, "SKILL.md"] = "retain my personal skill"
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, self.resources, self.path)
        self.assertEqual(len(self.host.skills.files), 1)
        self.assertEqual(self.host.skills.published, [])

    def test_gate_rejection_is_not_a_publication_success(self):
        self.host.skills.reject_gate = True
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, self.resources, self.path)
        self.assertEqual(self.host.skills.published, [])
        self.assertIn("FAILED_OR_INCOMPLETE", self.path.read_text())

    def reject_before_host(self, resources):
        # A malformed plan must fail before even looking up host inventory.
        with self.assertRaises(ValueError):
            SETUP["install"](None, resources, self.path)
        self.assertFalse(self.path.exists())

    def test_single_bundle_guard_remains_and_batch_plan_is_complete(self):
        with self.assertRaisesRegex(ValueError, "1 MB"):
            SETUP["bundle"](ROOT)
        single = SETUP["bundle"](ROOT, [SETUP["NAMES"][-1]])
        self.assertEqual(list(single), [SETUP["NAMES"][-1]])
        seen = [name for batch in self.resources for name in batch]
        self.assertEqual(seen, SETUP["NAMES"])
        self.assertEqual(len(seen), len(set(seen)))

    def test_flattening_cannot_bypass_the_single_batch_guard(self):
        flat = {name: files for batch in self.resources for name, files in batch.items()}
        self.reject_before_host(flat)

    def test_duplicate_missing_unknown_or_excessive_batches_rejected(self):
        duplicate = copy.deepcopy(self.resources)
        duplicate[-1][SETUP["NAMES"][0]] = duplicate[0][SETUP["NAMES"][0]]
        self.reject_before_host(duplicate)
        missing = copy.deepcopy(self.resources)
        del missing[-1][SETUP["NAMES"][-1]]
        self.reject_before_host(missing)
        unknown = copy.deepcopy(self.resources)
        unknown[-1]["unreviewed"] = {"SKILL.md": "name: unreviewed"}
        self.reject_before_host(unknown)
        self.reject_before_host(self.resources * 9)

    def test_oversize_skill_and_noncanonical_paths_rejected(self):
        for path, content in [("large.txt", "x" * 200001), ("../escape.txt", "x"),
                              ("a//b", "x"), ("a\\b", "x"), ("a:b", "x"),
                              ("/absolute", "x"), ("nontext", 1), ("unicode.txt", "\u03bb" * 100001)]:
            with self.subTest(path=path):
                bad = copy.deepcopy(self.resources)
                bad[-1][SETUP["NAMES"][-1]][path] = content
                self.reject_before_host(bad)

    def test_wrong_declared_name_rejected_before_host(self):
        bad = copy.deepcopy(self.resources)
        bad[-1][SETUP["NAMES"][-1]]["SKILL.md"] = "name: collision"
        self.reject_before_host(bad)


if __name__ == "__main__":
    unittest.main()
