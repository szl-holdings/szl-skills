"""Synthetic, offline acceptance tests for two-cutoff rare-disease evidence deltas."""
# SPDX-License-Identifier: Apache-2.0

import copy
import hashlib
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-rare-disease-evidence-replay"
SCRIPT = SKILL / "scripts" / "replay.py"
SPEC = importlib.util.spec_from_file_location("szl_rare_disease_evidence_delta", SCRIPT)
REPLAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPLAY)


class RareDiseaseEvidenceDeltaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="szl-rare-delta-")
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        shutil.copytree(SKILL / "assets", self.root / "assets")
        original = json.loads((self.root / "assets/manifest.json").read_text(encoding="utf-8"))
        self.earlier = copy.deepcopy(original)
        self.earlier["cutoff_utc"] = "2026-01-15T00:00:00Z"
        self.later = copy.deepcopy(original)
        self.later["cutoff_utc"] = "2026-02-03T00:00:00Z"
        self.save_manifests()

    def save_manifests(self):
        for path, document in (("earlier.json", self.earlier), ("later.json", self.later)):
            (self.root / "assets" / path).write_text(
                json.dumps(document, indent=2) + "\n", encoding="utf-8")

    def result(self):
        return REPLAY.compare_replays(self.root, "assets/earlier.json", "assets/later.json")

    def hold_reason(self):
        with self.assertRaises(REPLAY.Hold) as raised:
            self.result()
        return str(raised.exception)

    def replace_snapshot(self, source_index, snapshot_index, document):
        reference = self.earlier["sources"][source_index]["snapshots"][snapshot_index]
        raw = (json.dumps(document, indent=2) + "\n").encode("utf-8")
        (self.root / reference["path"]).write_bytes(raw)
        reference["sha256"] = hashlib.sha256(raw).hexdigest()
        self.later["sources"][source_index]["snapshots"][snapshot_index]["sha256"] = reference["sha256"]
        self.save_manifests()

    def test_two_cutoffs_are_deterministic_and_preserve_declared_limits(self):
        first = self.result()
        self.assertEqual(first, self.result())
        self.assertEqual(first["status"], "DECLARED_ONLY")
        self.assertEqual(first["readiness"], "HOLD")
        self.assertEqual(first["qualification"], "NOT_EVALUATED")
        self.assertFalse(first["signed"])
        self.assertFalse(first["independent_witness"])
        self.assertEqual(first["earlier"]["selected_snapshots"][1]["snapshot_id"],
                         "source-a-current")
        self.assertEqual(first["later"]["selected_snapshots"][1]["snapshot_id"],
                         "source-a-future")
        self.assertEqual(first["deltas"]["case_features"]["added"][0]["identity"],
                         "SYNTH-HP:0003")
        self.assertEqual(first["deltas"]["case_features"]["added"][0]["after"]["state"],
                         "PRESENT")
        self.assertEqual(len(first["deltas"]["hpo_annotations"]["declared_removed"]), 3)
        self.assertEqual(len(first["deltas"]["hpo_annotations"]["added"]), 1)
        self.assertEqual(first["deltas"]["clinvar_scv"]["changed"], [])
        self.assertIn("DECLARED_TIME_NOT_WITNESSED", first["limits"])
        self.assertNotIn("rank", json.dumps(first).lower())

    def test_bundled_later_manifest_is_a_runnable_synthetic_example(self):
        report = REPLAY.compare_replays(SKILL, "assets/manifest.json",
                                        "assets/manifest-later.json")
        self.assertEqual(report["status"], "DECLARED_ONLY")
        self.assertEqual(report["later"]["cutoff_utc"], "2026-02-03T00:00:00Z")

    def test_row_and_accession_version_changes_are_not_interpreted(self):
        hpo = json.loads((self.root / "assets/source-a-future.json").read_text(encoding="utf-8"))
        hpo["annotations"][0]["qualifier"] = "ANNOTATED"
        hpo["annotations"][0]["source_ref"] = "SYNTH-REF:STUDY-A"
        self.replace_snapshot(0, 2, hpo)

        clinvar = json.loads((self.root / "assets/source-b-current.json").read_text(encoding="utf-8"))
        clinvar["release"] = "SYNTHETIC-CLINVAR-2"
        clinvar["records"] = clinvar["records"][:1]
        record = clinvar["records"][0]
        record["vcv"] = "SYNTH-VCV:0001.2"
        record["rcv"] = "SYNTH-RCV:0001.2"
        record["observed_at_utc"] = "2026-02-02T00:00:00Z"
        record["submissions"][0]["scv"] = "SYNTH-SCV:0001.2"
        record["submissions"][0]["assertion_token"] = "SYNTH-ASSERT:0004"
        record["submissions"][0]["observed_at_utc"] = "2026-02-02T00:00:00Z"
        raw = (json.dumps(clinvar, indent=2) + "\n").encode("utf-8")
        (self.root / "assets/source-b-later.json").write_bytes(raw)
        new_snapshot = {
            "id": "source-b-later", "captured_at_utc": "2026-02-02T00:00:00Z",
            "release": "SYNTHETIC-CLINVAR-2", "path": "assets/source-b-later.json",
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        self.earlier["sources"][1]["snapshots"].append(new_snapshot)
        self.later["sources"][1]["snapshots"].append(copy.deepcopy(new_snapshot))
        self.save_manifests()

        delta = self.result()["deltas"]
        self.assertEqual(len(delta["hpo_annotations"]["changed"]), 1)
        self.assertEqual(delta["hpo_annotations"]["changed"][0]["before"]["observed_at_utc"],
                         "2026-01-08T00:00:00Z")
        self.assertEqual(delta["clinvar_vcv"]["changed"][0]["after"]["vcv"],
                         "SYNTH-VCV:0001.2")
        self.assertEqual(delta["clinvar_rcv"]["changed"][0]["after"]["rcv"],
                         "SYNTH-RCV:0001.2")
        self.assertEqual(delta["clinvar_scv"]["changed"][0]["after"]["assertion_token"],
                         "SYNTH-ASSERT:0004")
        self.assertEqual(delta["clinvar_scv"]["declared_removed"][0]["identity"],
                         "SYNTH-SCV:0003")

    def test_future_snapshot_bytes_are_not_opened_when_both_cutoffs_precede_it(self):
        self.earlier["cutoff_utc"] = "2026-01-09T00:00:00Z"
        self.later["cutoff_utc"] = "2026-01-15T00:00:00Z"
        self.save_manifests()
        (self.root / "assets/source-a-future.json").unlink()
        self.assertEqual(self.result()["status"], "DECLARED_ONLY")

    def test_later_incomplete_snapshot_holds_without_fallback(self):
        document = json.loads((self.root / "assets/source-a-future.json").read_text(encoding="utf-8"))
        document["complete"] = False
        self.replace_snapshot(0, 2, document)
        self.assertEqual(self.hold_reason(), "LATEST_SNAPSHOT_INCOMPLETE")

    def test_changed_selected_bytes_and_mismatched_inputs_hold(self):
        (self.root / "assets/source-a-future.json").write_bytes(b"{}\n")
        self.assertEqual(self.hold_reason(), "PIN_MISMATCH")
        self.later["case"]["sha256"] = "0" * 64
        self.save_manifests()
        self.assertEqual(self.hold_reason(), "COMPARISON_INPUT_MISMATCH")

    def test_manifest_change_between_validation_and_replay_holds(self):
        original = REPLAY._replay_with_rows

        def change_later_after_earlier(root, path):
            result = original(root, path)
            if path == "assets/earlier.json":
                self.later["cutoff_utc"] = "2026-02-04T00:00:00Z"
                self.save_manifests()
            return result

        with mock.patch.object(REPLAY, "_replay_with_rows",
                               side_effect=change_later_after_earlier):
            self.assertEqual(self.hold_reason(), "COMPARISON_INPUT_CHANGED")

    def test_equal_cutoff_and_ambiguous_latest_snapshot_hold(self):
        self.later["cutoff_utc"] = self.earlier["cutoff_utc"]
        self.save_manifests()
        self.assertEqual(self.hold_reason(), "INVALID_CUTOFF_ORDER")
        self.later["cutoff_utc"] = "2026-02-03T00:00:00Z"
        self.earlier["sources"][0]["snapshots"][1]["captured_at_utc"] = "2026-02-01T00:00:00Z"
        self.later["sources"][0]["snapshots"][1]["captured_at_utc"] = "2026-02-01T00:00:00Z"
        self.save_manifests()
        self.assertEqual(self.hold_reason(), "AMBIGUOUS_LATEST_SNAPSHOT")

    def test_two_versions_of_one_accession_stem_hold_in_delta_only(self):
        clinvar = json.loads((self.root / "assets/source-b-current.json").read_text(encoding="utf-8"))
        duplicate = copy.deepcopy(clinvar["records"][0])
        duplicate["rcv"] = "SYNTH-RCV:0001.2"
        duplicate["submissions"][0]["scv"] = "SYNTH-SCV:0004.1"
        duplicate["submissions"][1]["scv"] = "SYNTH-SCV:0005.1"
        clinvar["records"].append(duplicate)
        self.replace_snapshot(1, 0, clinvar)
        self.assertEqual(self.hold_reason(), "AMBIGUOUS_DELTA_IDENTITY")

    def test_cli_creates_new_report_and_hold_has_no_evidence_delta(self):
        command = [sys.executable, "-I", "-B", str(SCRIPT), "--root", str(self.root),
                   "--manifest", "assets/earlier.json", "--compare-manifest", "assets/later.json",
                   "--output", "delta.json"]
        first = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(first.stdout),
                         json.loads((self.root / "delta.json").read_text(encoding="utf-8")))
        second = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(second.returncode, 2)
        self.later["cutoff_utc"] = self.earlier["cutoff_utc"]
        self.save_manifests()
        command[-1] = "hold.json"
        failed = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(failed.returncode, 2)
        hold = json.loads((self.root / "hold.json").read_text(encoding="utf-8"))
        self.assertEqual(hold["reason"], "INVALID_CUTOFF_ORDER")
        self.assertNotIn("deltas", hold)


if __name__ == "__main__":
    unittest.main()
