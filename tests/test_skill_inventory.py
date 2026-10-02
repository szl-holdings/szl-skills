"""Source inventory checks; skill payloads are inspected as text, never run."""
import json
import pathlib
import tempfile
import unittest

from tools import skill_inventory

ROOT = pathlib.Path(__file__).resolve().parents[1]


class InventoryTests(unittest.TestCase):
    def test_committed_inventory_matches_tree_and_marketplace(self):
        skill_inventory.check(ROOT)
        report = skill_inventory.build(ROOT)
        self.assertEqual(report["counts"]["total"],
                         sum(report["counts"]["by_plugin"].values()))
        self.assertEqual(set(report["plugins"]), set(report["counts"]["by_plugin"]))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        (self.root / ".claude-plugin").mkdir()
        (self.root / "tools").mkdir()
        self.set_installer(["szl-one"])
        (self.root / "skills" / "szl-one").mkdir(parents=True)
        (self.root / "skills" / "szl-one" / "SKILL.md").write_text(
            "---\nname: szl-one\ndescription: fixture\n---\n", encoding="utf-8")
        self.market = {"plugins": [{"name": "szl-science-skills", "skills": ["./skills/szl-one"]},
                                   {"name": "szl-evidence-skills", "skills": []}]}
        self.save_market()

    def save_market(self):
        (self.root / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps(self.market), encoding="utf-8")

    def set_installer(self, names):
        (self.root / "tools" / "install_claude_science.py").write_text(
            "NAMES = " + repr(names) + "\n", encoding="utf-8")

    def test_new_skill_requires_catalog_and_regeneration(self):
        (self.root / skill_inventory.OUTPUT).write_text(skill_inventory.render(self.root), encoding="utf-8")
        (self.root / "skills" / "szl-two").mkdir()
        (self.root / "skills" / "szl-two" / "SKILL.md").write_text(
            "---\nname: szl-two\ndescription: fixture\n---\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "uncataloged=\\['szl-two'\\]"):
            skill_inventory.build(self.root)
        self.market["plugins"][0]["skills"].append("./skills/szl-two")
        self.save_market()
        with self.assertRaisesRegex(ValueError, "installer/science catalog mismatch"):
            skill_inventory.build(self.root)
        self.set_installer(["szl-one", "szl-two"])
        with self.assertRaisesRegex(ValueError, "is stale"):
            skill_inventory.check(self.root)
        (self.root / skill_inventory.OUTPUT).write_text(skill_inventory.render(self.root), encoding="utf-8")
        skill_inventory.check(self.root)
        self.assertEqual(skill_inventory.build(self.root)["counts"]["total"], 2)

    def test_catalog_orphan_and_duplicate_membership_fail(self):
        self.market["plugins"][0]["skills"].append("./skills/szl-missing")
        self.save_market()
        with self.assertRaisesRegex(ValueError, "missing=\\['szl-missing'\\]"):
            skill_inventory.build(self.root)
        self.market["plugins"][0]["skills"].pop()
        self.market["plugins"][1]["skills"].append("./skills/szl-one")
        self.save_market()
        with self.assertRaisesRegex(ValueError, "duplicate marketplace skill"):
            skill_inventory.build(self.root)

    def test_frontmatter_name_must_match_package(self):
        (self.root / "skills" / "szl-one" / "SKILL.md").write_text(
            "---\nname: szl-other\ndescription: fixture\n---\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "frontmatter name mismatch"):
            skill_inventory.build(self.root)

    def test_duplicate_installer_selection_fails(self):
        self.set_installer(["szl-one", "szl-one"])
        with self.assertRaisesRegex(ValueError, "duplicate installer skill name"):
            skill_inventory.build(self.root)


if __name__ == "__main__":
    unittest.main()
