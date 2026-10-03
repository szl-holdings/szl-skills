"""The harmonizer must refuse ambiguous joins and changed source bytes."""

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
SKILL = ROOT / "skills" / "szl-measurement-harmonizer"
IMPL = runpy.run_path(str(SKILL / "scripts" / "harmonizer.py"))
HARMONIZE = IMPL["harmonize"]
EXAMPLE = json.loads((SKILL / "assets" / "example.json").read_text(encoding="utf-8"))


class MeasurementHarmonizerTests(unittest.TestCase):
    def test_checked_alignment_retains_raw_and_normalized_values(self):
        report = HARMONIZE(EXAMPLE, SKILL / "assets")
        self.assertEqual(report["status"], "HARMONIZED")
        self.assertEqual(len(report["rows"]), 4)
        self.assertEqual(report["scientific_validity"], "NOT_EVALUATED")
        groups = {}
        for row in report["rows"]:
            groups.setdefault(row["canonical_id"], []).append(row)
        self.assertEqual({row["normalized_value"] for row in groups["specimen-1"]}, {"298.15"})
        self.assertEqual({row["normalized_value"] for row in groups["specimen-2"]}, {"293.15"})
        self.assertEqual({row["raw_value"] for row in groups["specimen-1"]}, {"25", "298.15"})

    def test_changed_file_blocks_all_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            for name in ("lab-a.csv", "lab-b.csv"):
                (root / name).write_bytes((SKILL / "assets" / name).read_bytes())
            with (root / "lab-b.csv").open("ab") as stream:
                stream.write(b"B3,294.15\n")
            report = HARMONIZE(EXAMPLE, root)
            self.assertEqual(report["status"], "BLOCKED")
            self.assertEqual(report["rows"], [])
            self.assertIn("DIGEST_MISMATCH", [item["code"] for item in report["findings"]])
            self.assertEqual(report["input_sha256"]["lab-b"], hashlib.sha256((root / "lab-b.csv").read_bytes()).hexdigest())

    def test_missing_input_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            (pathlib.Path(temp) / "lab-a.csv").write_bytes((SKILL / "assets" / "lab-a.csv").read_bytes())
            report = HARMONIZE(EXAMPLE, temp)
            self.assertEqual(report["status"], "UNAVAILABLE")
            self.assertEqual(report["rows"], [])

    def test_ambiguous_join_and_incomplete_join_block(self):
        ambiguous = copy.deepcopy(EXAMPLE)
        ambiguous["sources"][0]["id_map"]["A-2"] = "specimen-1"
        report = HARMONIZE(ambiguous, SKILL / "assets")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("MANY_TO_ONE_ID_MAP", [item["code"] for item in report["findings"]])

        incomplete = copy.deepcopy(EXAMPLE)
        del incomplete["sources"][0]["id_map"]["A-2"]
        report = HARMONIZE(incomplete, SKILL / "assets")
        self.assertEqual(report["rows"], [])
        self.assertIn("UNMAPPED_ID", [item["code"] for item in report["findings"]])

    def test_rejects_unsafe_path_and_nonidentity_target_conversion(self):
        unsafe = copy.deepcopy(EXAMPLE)
        unsafe["sources"][0]["path"] = "../lab-a.csv"
        report = HARMONIZE(unsafe, SKILL / "assets")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("UNSAFE_PATH", [item["code"] for item in report["findings"]])

        conversion = copy.deepcopy(EXAMPLE)
        conversion["conversions"]["K"]["offset"] = "1"
        report = HARMONIZE(conversion, SKILL / "assets")
        self.assertEqual(report["rows"], [])
        self.assertIn("TARGET_NOT_IDENTITY", [item["code"] for item in report["findings"]])

    def test_cli_will_not_overwrite_input_or_existing_report(self):
        with tempfile.TemporaryDirectory() as temp:
            script = SKILL / "scripts" / "run.py"
            manifest = SKILL / "assets" / "example.json"
            input_path = SKILL / "assets" / "lab-a.csv"
            result = subprocess.run([sys.executable, "-B", str(script), str(manifest),
                                     "--root", str(SKILL / "assets"), "--output", str(input_path)],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(hashlib.sha256(input_path.read_bytes()).hexdigest(), EXAMPLE["sources"][0]["sha256"])

            existing = pathlib.Path(temp) / "report.json"
            existing.write_text("keep", encoding="utf-8")
            result = subprocess.run([sys.executable, "-B", str(script), str(manifest),
                                     "--root", str(SKILL / "assets"), "--output", str(existing)],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
