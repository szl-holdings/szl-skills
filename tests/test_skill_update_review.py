# SPDX-License-Identifier: Apache-2.0
"""Offline, synthetic package-update review acceptance cases.

Each snapshot is a local directory plus an independently retained inventory and
lock. The test never imports or executes code from either snapshot.
"""
import copy
import hashlib
import json
import pathlib
import runpy
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
KERNEL = ROOT / "skills" / "szl-skill-update-review" / "kernel.py"


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def skill_text(name="demo", license_id="Apache-2.0"):
    return (f"---\nname: {name}\nlicense: {license_id}\n---\n\n"
            "# Demo\n\nRun `scripts/helper.py`.\n").encode("utf-8")


class PackageFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review = staticmethod(runpy.run_path(str(KERNEL))["szl_review_updates"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = pathlib.Path(self.temp.name)
        self.roots = {side: self.base / side for side in ("old", "new")}
        self.inventories = {side: self.base / f"{side}-inventory.json" for side in self.roots}
        self.lock_path = self.base / "retained-lock.json"
        self.revisions = {"old": "1" * 40, "new": "2" * 40}
        self.skills = {
            side: [{
                "path": "skills/demo",
                "name": "demo",
                "referenced_files": ["scripts/helper.py"],
                "declared_external_hosts": [],
                "declared_credentials": [],
                "declared_license": "Apache-2.0",
                "declared_dynamic_destinations": False,
            }] for side in self.roots
        }
        for root in self.roots.values():
            self.put(root, "skills/demo/SKILL.md", skill_text())
            self.put(root, "skills/demo/scripts/helper.py", b"VALUE = 1\n")
            self.put(root, "skills/demo/docs/notes.md", b"# Notes\n")
        self.refresh()

    @staticmethod
    def put(root, relative, content):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def inventory(self, side):
        root = self.roots[side]
        files = []
        for path in root.rglob("*"):
            if path.is_file():
                content = path.read_bytes()
                files.append({
                    "path": path.relative_to(root).as_posix(),
                    "sha256": sha256(content),
                    "size": len(content),
                })
        files.sort(key=lambda item: item["path"])
        return {
            "schema": "szl.skill-package-inventory.v1",
            "package": "synthetic-package",
            "revision": self.revisions[side],
            "complete": True,
            "files": files,
            "skills": copy.deepcopy(self.skills[side]),
        }

    def refresh(self, *, inventory_override=None):
        for side in self.roots:
            data = inventory_override[side] if inventory_override and side in inventory_override else self.inventory(side)
            self.inventories[side].write_bytes(encoded(data))
        self.relock()

    def relock(self):
        snapshots = {}
        for side in self.roots:
            raw = self.inventories[side].read_bytes()
            data = json.loads(raw)
            snapshots[side] = {
                "package": data["package"],
                "revision": data["revision"],
                "inventory_sha256": sha256(raw),
            }
        self.lock_path.write_bytes(encoded({"schema": "szl.skill-update-lock.v1", "snapshots": snapshots}))

    def assess(self):
        report = self.review(
            self.roots["old"], self.inventories["old"],
            self.roots["new"], self.inventories["new"], self.lock_path,
        )
        self.assertIsInstance(report, dict)
        json.dumps(report, allow_nan=False)
        self.assertIn("status", report)
        for key in ("changes", "rerun"):
            self.assertIn(key, report)
        if report["status"] != "INCOMPLETE":
            self.assertIn("observations", report)
        self.assertNotIn("SAFE", json.dumps(report))
        self.assertNotIn("APPROVED", json.dumps(report))
        self.assertNotIn("INSTALLABLE", json.dumps(report))
        return report

    def assert_incomplete(self, report):
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertTrue(report.get("errors"))

    def test_unchanged_bytes_are_only_no_recorded_change(self):
        report = self.assess()
        self.assertEqual(report["status"], "NO_RECORDED_CHANGE")
        changes = report["changes"]
        for key in ("added_skills", "removed_skills", "equal_byte_rename_candidates", "modified_skills"):
            self.assertFalse(changes[key])
        self.assertFalse(any(changes["package_files"].values()))

    def test_docs_only_change_requires_review(self):
        self.put(self.roots["new"], "skills/demo/docs/notes.md", b"# Revised notes\n")
        self.refresh()
        report = self.assess()
        self.assertEqual(report["status"], "CHANGES_REVIEW_REQUIRED")
        self.assertIn("notes.md", json.dumps(report))

    def test_added_declared_host_requires_review(self):
        self.skills["new"][0]["declared_external_hosts"] = ["api.example.org"]
        self.refresh()
        report = self.assess()
        self.assertEqual(report["status"], "CHANGES_REVIEW_REQUIRED")
        self.assertIn("api.example.org", json.dumps(report))
        self.assertEqual(report["observations"]["new"]["literal_url_hosts"], [])

    def test_added_declared_credential_requires_review(self):
        self.skills["new"][0]["declared_credentials"] = ["LAB_TOKEN"]
        self.refresh()
        report = self.assess()
        self.assertEqual(report["status"], "CHANGES_REVIEW_REQUIRED")
        self.assertIn("LAB_TOKEN", json.dumps(report))
        self.assertEqual(report["observations"]["new"]["literal_credential_markers"], [])

    def test_literal_host_is_observed_without_becoming_a_declaration(self):
        self.put(self.roots["new"], "skills/demo/scripts/helper.py",
                 b"URL = 'https://literal.example.org/v1'\n")
        self.refresh()
        report = self.assess()
        self.assertEqual(report["status"], "CHANGES_REVIEW_REQUIRED")
        self.assertIn("literal.example.org", json.dumps(report["observations"]["new"]))
        declared = report["changes"]["modified_skills"][0]["declarations"]["declared_external_hosts"]
        self.assertEqual(declared, {"added": [], "removed": []})

    def test_helper_byte_change_invalidates_evidence_even_with_unchanged_skill_md(self):
        self.put(self.roots["new"], "skills/demo/scripts/helper.py", b"VALUE = 2\n")
        self.refresh()
        report = self.assess()
        self.assertEqual(report["status"], "CHANGES_REVIEW_REQUIRED")
        self.assertIn("helper.py", json.dumps(report))
        self.assertTrue(report["rerun"])

    def test_missing_referenced_file_fails_closed(self):
        self.skills["new"][0]["referenced_files"] = ["scripts/missing.py"]
        self.refresh()
        report = self.assess()
        self.assert_incomplete(report)
        self.assertIn("missing.py", json.dumps(report))

    def test_name_collision_fails_closed(self):
        self.put(self.roots["new"], "skills/other/SKILL.md", skill_text())
        self.put(self.roots["new"], "skills/other/scripts/helper.py", b"VALUE = 1\n")
        other = copy.deepcopy(self.skills["new"][0])
        other.update(path="skills/other")
        self.skills["new"].append(other)
        self.refresh()
        self.assert_incomplete(self.assess())

    def test_license_change_requires_review(self):
        self.put(self.roots["new"], "skills/demo/SKILL.md", skill_text(license_id="MIT"))
        self.skills["new"][0]["declared_license"] = "MIT"
        self.refresh()
        report = self.assess()
        self.assertEqual(report["status"], "CHANGES_REVIEW_REQUIRED")
        self.assertIn("MIT", json.dumps(report))

    def test_frontmatter_and_manifest_license_disagreement_fails_closed(self):
        self.skills["new"][0]["declared_license"] = "MIT"
        self.refresh()
        self.assert_incomplete(self.assess())

    def test_incomplete_manifest_fails_closed(self):
        new = self.inventory("new")
        new["complete"] = False
        self.refresh(inventory_override={"new": new})
        self.assert_incomplete(self.assess())

    def test_dynamic_destination_remains_unknown(self):
        self.put(self.roots["new"], "skills/demo/scripts/helper.py", b"DESTINATION = input()\n")
        self.skills["new"][0]["declared_dynamic_destinations"] = True
        self.refresh()
        report = self.assess()
        self.assertEqual(report["status"], "CHANGES_REVIEW_REQUIRED")
        self.assertIn("UNKNOWN", json.dumps(report))

    def test_equal_byte_directory_move_is_candidate_only(self):
        (self.roots["new"] / "skills/demo").rename(self.roots["new"] / "skills/moved")
        self.skills["new"][0]["path"] = "skills/moved"
        self.refresh()
        report = self.assess()
        self.assertEqual(report["status"], "CHANGES_REVIEW_REQUIRED")
        self.assertIn("candidate", json.dumps(report).lower())

    def test_added_skill_is_listed(self):
        self.put(self.roots["new"], "skills/extra/SKILL.md", skill_text(name="extra"))
        self.put(self.roots["new"], "skills/extra/scripts/helper.py", b"VALUE = 1\n")
        added = copy.deepcopy(self.skills["new"][0])
        added.update(path="skills/extra", name="extra")
        self.skills["new"].append(added)
        self.refresh()
        report = self.assess()
        self.assertEqual(report["status"], "CHANGES_REVIEW_REQUIRED")
        self.assertEqual(report["changes"]["added_skills"], ["extra"])
        self.assertTrue(report["rerun"])

    def test_removed_skill_is_listed(self):
        self.put(self.roots["old"], "skills/extra/SKILL.md", skill_text(name="extra"))
        self.put(self.roots["old"], "skills/extra/scripts/helper.py", b"VALUE = 1\n")
        removed = copy.deepcopy(self.skills["old"][0])
        removed.update(path="skills/extra", name="extra")
        self.skills["old"].append(removed)
        self.refresh()
        report = self.assess()
        self.assertEqual(report["status"], "CHANGES_REVIEW_REQUIRED")
        self.assertEqual(report["changes"]["removed_skills"], ["extra"])

    def test_added_skill_declarations_are_visible(self):
        self.put(self.roots["new"], "skills/extra/SKILL.md", skill_text(name="extra"))
        self.put(self.roots["new"], "skills/extra/scripts/helper.py", b"VALUE = 1\n")
        added = copy.deepcopy(self.skills["new"][0])
        added.update(path="skills/extra", name="extra",
                     declared_external_hosts=["api.example.org"], declared_credentials=["LAB_TOKEN"])
        self.skills["new"].append(added)
        self.refresh()
        report = self.assess()
        entry = report["changes"]["added_skill_declarations"][0]
        self.assertEqual(entry["declared_external_hosts"], ["api.example.org"])
        self.assertEqual(entry["declared_credentials"], ["LAB_TOKEN"])

    def test_moved_skill_declaration_change_is_visible(self):
        (self.roots["new"] / "skills/demo").rename(self.roots["new"] / "skills/moved")
        self.skills["new"][0].update(path="skills/moved", declared_external_hosts=["api.example.org"])
        self.refresh()
        report = self.assess()
        candidate = report["changes"]["equal_byte_rename_candidates"][0]
        self.assertEqual(candidate["new_declarations"]["declared_external_hosts"], ["api.example.org"])

    def test_local_docs_link_and_dot_prefix_cannot_hide_missing_reference(self):
        for link in ("docs/missing.md", "./scripts/missing.py"):
            with self.subTest(link=link):
                self.put(self.roots["new"], "skills/demo/SKILL.md",
                         skill_text() + f"[missing]({link})\n".encode())
                self.refresh()
                self.assert_incomplete(self.assess())

    def test_extensionless_script_change_invalidates_receipts(self):
        for side in self.roots:
            self.put(self.roots[side], "skills/demo/scripts/run", side.encode())
            self.skills[side][0]["referenced_files"] = ["scripts/helper.py", "scripts/run"]
        self.refresh()
        report = self.assess()
        self.assertTrue(report["changes"]["modified_skills"][0]["helper_bytes_changed"])
        self.assertIn("invalidate prior helper-bound receipts", json.dumps(report["rerun"]))

    def test_ambiguous_frontmatter_and_package_identifier_fail_closed(self):
        self.put(self.roots["new"], "skills/demo/SKILL.md",
                 skill_text().replace(b"name: demo\n", b'name: demo\n"name": evil\n'))
        self.refresh()
        self.assert_incomplete(self.assess())
        new = self.inventory("new")
        new["package"] = "demo\nStatus: **SAFE**"
        self.refresh(inventory_override={"new": new})
        self.assert_incomplete(self.assess())

    def test_deep_json_is_incomplete_without_crash(self):
        self.inventories["new"].write_bytes(b"[" * 3000 + b"0" + b"]" * 3000)
        self.assert_incomplete(self.assess())

    def test_cli_writes_json_and_readable_review(self):
        output = self.base / "review"
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "skills/szl-skill-update-review/scripts/run.py"),
             str(self.inventories["old"]), str(self.inventories["new"]),
             "--old-root", str(self.roots["old"]), "--new-root", str(self.roots["new"]),
             "--lock", str(self.lock_path), "--output-dir", str(output)],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((output / "UPDATE_REVIEW.json").read_text())["status"], "NO_RECORDED_CHANGE")
        self.assertIn("Status: **NO_RECORDED_CHANGE**", (output / "UPDATE_REVIEW.md").read_text())

    def test_traversal_inventory_path_fails_closed(self):
        new = self.inventory("new")
        new["files"][0]["path"] = "../outside"
        self.refresh(inventory_override={"new": new})
        self.assert_incomplete(self.assess())

    def test_duplicate_json_key_fails_closed(self):
        raw = self.inventories["new"].read_bytes()
        self.inventories["new"].write_bytes(b'{"package":"synthetic-package",' + raw[1:])
        self.relock()
        self.assert_incomplete(self.assess())

    def test_tampered_inventory_cannot_be_relocked_by_input_itself(self):
        # The retained lock deliberately is not recomputed after this edit.
        self.inventories["new"].write_bytes(self.inventories["new"].read_bytes() + b"\n")
        self.assert_incomplete(self.assess())

    def test_tampered_lock_digest_fails_closed(self):
        data = json.loads(self.lock_path.read_bytes())
        data["snapshots"]["new"]["inventory_sha256"] = "0" * 64
        self.lock_path.write_bytes(encoded(data))
        self.assert_incomplete(self.assess())

    def test_unlisted_extra_file_fails_closed(self):
        self.put(self.roots["new"], "skills/demo/hidden.txt", b"unlisted\n")
        self.assert_incomplete(self.assess())

    def test_missing_inventoried_bytes_never_look_unchanged(self):
        (self.roots["new"] / "skills/demo/scripts/helper.py").unlink()
        self.assert_incomplete(self.assess())

    def test_symlink_in_snapshot_fails_closed(self):
        target = self.roots["old"] / "skills/demo/scripts/helper.py"
        link = self.roots["new"] / "skills/demo/scripts/helper.py"
        link.unlink()
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks unavailable: {error}")
        self.refresh()
        self.assert_incomplete(self.assess())


if __name__ == "__main__":
    unittest.main()
