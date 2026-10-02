"""Documented SDK contract tests only. This test double is NOT the real application."""
import pathlib
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
        self.resources = SETUP["bundle"](ROOT)

    def tearDown(self):
        self.temp.cleanup()

    def test_supported_calls_publish_readback_and_curated_profile(self):
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        science = next(p for p in market["plugins"] if p["name"] == "szl-science-skills")
        evidence = next(p for p in market["plugins"] if p["name"] == "szl-evidence-skills")
        expected = {pathlib.PurePosixPath(p).name for p in science["skills"]}
        self.assertEqual(set(self.resources), expected)
        self.assertEqual(len(SETUP["NAMES"]), len(expected))
        self.assertTrue(expected.isdisjoint(pathlib.PurePosixPath(p).name for p in evidence["skills"]))
        result = SETUP["install"](self.host, self.resources, self.path)
        self.assertEqual(result["status"], "PUBLISHED_AND_READ_BACK")
        self.assertEqual(len(result["skills"]), len(SETUP["NAMES"]))
        self.assertEqual(result["agent"]["connectors"], [])
        self.assertFalse(result["agent"]["unrestricted"])
        self.assertEqual(result["runtime_task_evaluation"], "NOT_EXECUTED")
        second = SETUP["install"](self.host, self.resources, self.path.with_name("second.json"))
        self.assertEqual(second["status"], "PUBLISHED_AND_READ_BACK")
        self.assertEqual(len(self.host.skills.published), len(SETUP["NAMES"]))

    def test_collision_preflight_does_not_modify_other_resources(self):
        name = SETUP["NAMES"][-1]
        self.host.skills.origins[name] = "personal"
        self.host.skills.files[name, "SKILL.md"] = "retain my personal skill"
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, self.resources, self.path)
        self.assertEqual(len(self.host.skills.files), 1)
        self.assertEqual(self.host.skills.published, [])

    def test_two_bounded_families_attach_all_tools_without_replacing_profile(self):
        replay = SETUP["bundle"](ROOT, family="replay")
        self.assertEqual(set(replay), {"szl-experiment-replay"})
        for resources in (self.resources, replay):
            self.assertLessEqual(sum(len(v.encode()) for files in resources.values() for v in files.values()), 1000000)
        SETUP["install"](self.host, self.resources, self.path)
        result = SETUP["install"](self.host, replay, self.path.with_name("replay.json"), family="replay")
        self.assertEqual(set(result["agent"]["skillNames"]), set(SETUP["NAMES"] + SETUP["REPLAY_NAMES"]))
        self.assertEqual(result["family"], "replay")
        self.assertFalse(result["agent"]["unrestricted"])
        self.assertEqual(result["agent"]["connectors"], [])

    def test_unknown_partial_or_oversized_family_never_writes(self):
        with self.assertRaises(ValueError):
            SETUP["bundle"](ROOT, family="unreviewed")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {}, self.path, family="replay")
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, {"szl-experiment-replay": {"huge": "x" * 1000001}}, self.path, family="replay")
        self.assertEqual(self.host.skills.files, {})
        self.assertEqual(self.host.agents.profiles, {})

    def test_gate_rejection_is_not_a_publication_success(self):
        self.host.skills.reject_gate = True
        with self.assertRaises(ValueError):
            SETUP["install"](self.host, self.resources, self.path)
        self.assertEqual(self.host.skills.published, [])
        self.assertIn("FAILED_OR_INCOMPLETE", self.path.read_text())


if __name__ == "__main__":
    unittest.main()
