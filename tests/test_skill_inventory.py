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
        skill_inventory.write(self.root)
        skill_inventory.check(self.root)
        self.assertEqual(skill_inventory.build(self.root)["counts"]["total"], 2)

    def test_replay_family_requires_an_exact_separate_installer_selection(self):
        directory = self.root / "skills" / "szl-replay"; directory.mkdir()
        (directory / "SKILL.md").write_text("---\nname: szl-replay\ndescription: fixture\n---\n")
        self.market["plugins"].append({"name": "szl-science-replay-skills", "skills": ["./skills/szl-replay"]})
        self.save_market()
        with self.assertRaisesRegex(ValueError, "installer/replay catalog mismatch"):
            skill_inventory.build(self.root)
        installer = self.root / "tools" / "install_claude_science.py"
        installer.write_text(installer.read_text() + "REPLAY_NAMES = ['szl-replay']\n")
        self.assertEqual(skill_inventory.build(self.root)["counts"]["total"], 2)

    def test_design_family_requires_an_exact_separate_installer_selection(self):
        directory = self.root / "skills" / "szl-design"; directory.mkdir()
        (directory / "SKILL.md").write_text("---\nname: szl-design\ndescription: fixture\n---\n")
        self.market["plugins"].append({"name": "szl-science-design-skills", "skills": ["./skills/szl-design"]})
        self.save_market()
        with self.assertRaisesRegex(ValueError, "installer/design catalog mismatch"):
            skill_inventory.build(self.root)
        installer = self.root / "tools" / "install_claude_science.py"
        installer.write_text(installer.read_text() + "DESIGN_NAMES = ['szl-design']\n")
        self.assertEqual(skill_inventory.build(self.root)["counts"]["total"], 2)

    def test_paper_family_requires_an_exact_separate_installer_selection(self):
        directory = self.root / "skills" / "szl-paper"; directory.mkdir()
        (directory / "SKILL.md").write_text("---\nname: szl-paper\ndescription: fixture\n---\n")
        self.market["plugins"].append({"name": "szl-paper-evidence-skills", "skills": ["./skills/szl-paper"]})
        self.save_market()
        with self.assertRaisesRegex(ValueError, "installer/paper catalog mismatch"):
            skill_inventory.build(self.root)
        installer = self.root / "tools" / "install_claude_science.py"
        installer.write_text(installer.read_text() + "PAPER_NAMES = ['szl-paper']\n")
        self.assertEqual(skill_inventory.build(self.root)["counts"]["total"], 2)
        installer.write_text(installer.read_text().replace("['szl-paper']", "['szl-one']"))
        with self.assertRaisesRegex(ValueError, "installer/paper catalog mismatch"):
            skill_inventory.build(self.root)

    def test_assay_family_requires_an_exact_separate_installer_selection(self):
        directory = self.root / "skills" / "szl-assay"; directory.mkdir()
        (directory / "SKILL.md").write_text("---\nname: szl-assay\ndescription: fixture\n---\n")
        self.market["plugins"].append({"name": "szl-science-assay-skills", "skills": ["./skills/szl-assay"]})
        self.save_market()
        with self.assertRaisesRegex(ValueError, "installer/assay catalog mismatch"):
            skill_inventory.build(self.root)
        installer = self.root / "tools" / "install_claude_science.py"
        installer.write_text(installer.read_text() + "ASSAY_NAMES = ['szl-assay']\n")
        self.assertEqual(skill_inventory.build(self.root)["counts"]["total"], 2)

    def test_change_family_requires_an_exact_separate_installer_selection(self):
        directory = self.root / "skills" / "szl-change"; directory.mkdir()
        (directory / "SKILL.md").write_text("---\nname: szl-change\ndescription: fixture\n---\n")
        self.market["plugins"].append({"name": "szl-science-change-impact-skills", "skills": ["./skills/szl-change"]})
        self.save_market()
        with self.assertRaisesRegex(ValueError, "installer/change catalog mismatch"):
            skill_inventory.build(self.root)
        installer = self.root / "tools" / "install_claude_science.py"
        installer.write_text(installer.read_text() + "CHANGE_NAMES = ['szl-change']\n")
        self.assertEqual(skill_inventory.build(self.root)["counts"]["total"], 2)

    def test_multiplicity_family_requires_an_exact_separate_installer_selection(self):
        directory = self.root / "skills" / "szl-multiplicity"; directory.mkdir()
        (directory / "SKILL.md").write_text("---\nname: szl-multiplicity\ndescription: fixture\n---\n")
        self.market["plugins"].append({"name": "szl-science-multiplicity-skills", "skills": ["./skills/szl-multiplicity"]})
        self.save_market()
        with self.assertRaisesRegex(ValueError, "installer/multiplicity catalog mismatch"):
            skill_inventory.build(self.root)
        installer = self.root / "tools" / "install_claude_science.py"
        installer.write_text(installer.read_text() + "MULTIPLICITY_NAMES = ['szl-multiplicity']\n")
        self.assertEqual(skill_inventory.build(self.root)["counts"]["total"], 2)

    def test_uncertainty_family_requires_an_exact_separate_installer_selection(self):
        directory = self.root / "skills" / "szl-uncertainty"; directory.mkdir()
        (directory / "SKILL.md").write_text("---\nname: szl-uncertainty\ndescription: fixture\n---\n")
        self.market["plugins"].append({"name": "szl-science-uncertainty-skills", "skills": ["./skills/szl-uncertainty"]})
        self.save_market()
        with self.assertRaisesRegex(ValueError, "installer/uncertainty catalog mismatch"):
            skill_inventory.build(self.root)
        installer = self.root / "tools" / "install_claude_science.py"
        installer.write_text(installer.read_text() + "UNCERTAINTY_NAMES = ['szl-uncertainty']\n")
        self.assertEqual(skill_inventory.build(self.root)["counts"]["total"], 2)
        installer.write_text(installer.read_text().replace("['szl-uncertainty']", "['szl-one']"))
        with self.assertRaisesRegex(ValueError, "installer/uncertainty catalog mismatch"):
            skill_inventory.build(self.root)

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

    def test_repeated_frontmatter_name_fails_even_if_values_match(self):
        entry = self.root / "skills" / "szl-one" / "SKILL.md"
        for second in ("szl-one", "szl-other", ""):
            with self.subTest(second=second):
                entry.write_text("---\nname: szl-one\nname: " + second +
                                 "\ndescription: fixture\n---\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "frontmatter name mismatch"):
                    skill_inventory.build(self.root)

    def test_stray_directory_and_catalog_traversal_fail(self):
        (self.root / "skills" / "szl-stray").mkdir()
        with self.assertRaisesRegex(ValueError, "missing regular SKILL.md"):
            skill_inventory.build(self.root)
        (self.root / "skills" / "szl-stray").rmdir()
        self.market["plugins"][0]["skills"] = ["./skills/../szl-one"]
        self.save_market()
        with self.assertRaisesRegex(ValueError, "invalid marketplace skill path"):
            skill_inventory.build(self.root)

    def test_symlinked_skill_entry_and_output_fail(self):
        entry = self.root / "skills" / "szl-one" / "SKILL.md"
        target = self.root / "target.md"
        target.write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
        entry.unlink()
        try:
            entry.symlink_to(target)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        with self.assertRaisesRegex(ValueError, "missing regular SKILL.md"):
            skill_inventory.build(self.root)
        entry.unlink()
        entry.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        output = self.root / skill_inventory.OUTPUT
        output_target = self.root / "outside.json"
        output_target.write_text("retain", encoding="utf-8")
        output.symlink_to(output_target)
        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            skill_inventory.check(self.root)
        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            skill_inventory.write(self.root)
        self.assertEqual(output_target.read_text(encoding="utf-8"), "retain")

    def test_symlinked_skill_directory_fails(self):
        target = self.root / "other-skill"
        target.mkdir()
        link = self.root / "skills" / "szl-linked"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        with self.assertRaisesRegex(ValueError, "unexpected skills/ entry"):
            skill_inventory.build(self.root)

    def test_duplicate_installer_selection_fails(self):
        self.set_installer(["szl-one", "szl-one"])
        with self.assertRaisesRegex(ValueError, "duplicate installer skill name"):
            skill_inventory.build(self.root)


if __name__ == "__main__":
    unittest.main()
