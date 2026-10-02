"""Synthetic, offline acceptance tests for rare-disease evidence replay."""
# SPDX-License-Identifier: Apache-2.0

import hashlib
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-rare-disease-evidence-replay"
SCRIPT = SKILL / "scripts" / "replay.py"
SPEC = importlib.util.spec_from_file_location("szl_rare_disease_evidence_replay", SCRIPT)
REPLAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPLAY)


class RareDiseaseEvidenceReplayTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="szl-rare-replay-")
        self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name)
        shutil.copytree(SKILL / "assets", self.root / "assets")
        self.manifest_path = self.root / "assets" / "manifest.json"
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2) + "\n", encoding="utf-8")

    def replace_snapshot(self, source, snapshot_index, document):
        reference = self.manifest["sources"][source]["snapshots"][snapshot_index]
        raw = (json.dumps(document, indent=2) + "\n").encode("utf-8")
        (self.root / reference["path"]).write_bytes(raw)
        reference["sha256"] = hashlib.sha256(raw).hexdigest()
        self.save_manifest()

    def replace_case(self, document):
        raw = (json.dumps(document, indent=2) + "\n").encode("utf-8")
        (self.root / self.manifest["case"]["path"]).write_bytes(raw)
        self.manifest["case"]["sha256"] = hashlib.sha256(raw).hexdigest()
        self.save_manifest()

    def result(self):
        return REPLAY.replay(self.root, "assets/manifest.json")

    def hold_reason(self):
        with self.assertRaises(REPLAY.Hold) as raised:
            self.result()
        return str(raised.exception)

    def test_latest_eligible_and_source_provenance_ablation_are_deterministic(self):
        first = self.result()
        self.assertEqual(first, self.result())
        self.assertEqual(first["status"], "DECLARED_ONLY")
        self.assertEqual(first["readiness"], "HOLD")
        self.assertEqual(first["qualification"], "NOT_EVALUATED")
        self.assertFalse(first["signed"])
        self.assertFalse(first["independent_witness"])
        self.assertEqual(first["future_snapshots_excluded"], 1)
        self.assertEqual(first["future_case_features_excluded"], 1)
        self.assertEqual([row["snapshot_id"] for row in first["selected_snapshots"]],
                         ["source-b-current", "source-a-current"])
        self.assertEqual(first["case_features_at_cutoff"], [
            {"term": "SYNTH-HP:0001", "state": "PRESENT", "recorded_at_utc": "2026-01-08T00:00:00Z"},
            {"term": "SYNTH-HP:0002", "state": "EXCLUDED", "recorded_at_utc": "2026-01-09T00:00:00Z"},
        ])
        condition = first["base_evidence"][0]
        self.assertEqual((condition["condition"], condition["hpo_annotation_rows"],
                          condition["hpo_explicit_negative_rows"], condition["clinvar_rcv_records"],
                          condition["clinvar_scv_submissions"]),
                         ("SYNTH-COND:0001", 2, 1, 1, 2))
        self.assertEqual(condition["case_feature_annotation_links"][1]["case_state"], "EXCLUDED")
        self.assertEqual(condition["case_feature_annotation_links"][1]["annotation_qualifier"],
                         "EXPLICIT_NEGATIVE_ANNOTATION")
        self.assertEqual(condition["clinvar_scopes"][0]["vcv"], "SYNTH-VCV:0001.1")
        self.assertEqual(condition["clinvar_scopes"][0]["rcv"], "SYNTH-RCV:0001.1")
        self.assertEqual([row["scv"] for row in condition["clinvar_scopes"][0]["submissions"]],
                         ["SYNTH-SCV:0001.1", "SYNTH-SCV:0002.1"])
        self.assertEqual(first["base_evidence"][1]["declared_missing_sources"],
                         ["CLINVAR_ASSERTIONS"])
        self.assertEqual(first["base_evidence"][1]["case_feature_annotation_links"], [])
        self.assertEqual(first["base_evidence"][2]["declared_missing_sources"],
                         ["HPO_ANNOTATIONS"])
        self.assertEqual(first["leave_one_source_out"]["hpo-source"][0]["hpo_annotation_rows"], 0)
        self.assertEqual(first["leave_one_source_out"]["clinvar-source"][0]["clinvar_scv_submissions"], 0)
        self.assertEqual(len(first["leave_one_source_out"]["hpo-source"]), 3)
        self.assertEqual(len(first["leave_one_source_out"]["clinvar-source"]), 3)
        self.assertEqual(first["leave_one_source_out"]["hpo-source"][1]["declared_missing_sources"],
                         ["HPO_ANNOTATIONS", "CLINVAR_ASSERTIONS"])
        self.assertEqual(first["leave_one_provenance_out"]["SYNTH-REF:STUDY-A"][0]["hpo_annotation_rows"], 1)
        self.assertEqual(first["leave_one_provenance_out"]["SYNTH-REF:SUBMISSION-A"][0]["clinvar_scv_submissions"], 1)
        self.assertEqual(len(first["leave_one_provenance_out"]["SYNTH-REF:STUDY-C"]), 3)
        self.assertEqual(first["case_sha256"], hashlib.sha256((self.root / "assets/case.json").read_bytes()).hexdigest())
        self.assertEqual(first["manifest_sha256"], hashlib.sha256(self.manifest_path.read_bytes()).hexdigest())
        self.assertNotIn("rank", json.dumps(first).lower())
        self.assertNotIn("clinical_conflict", json.dumps(first).lower())

    def test_latest_case_feature_state_wins_without_inferred_absence(self):
        document = json.loads((self.root / "assets/case.json").read_text(encoding="utf-8"))
        document["features"].append({"term": "SYNTH-HP:0001", "state": "EXCLUDED",
                                     "recorded_at_utc": "2026-01-12T00:00:00Z"})
        self.replace_case(document)
        result = self.result()
        self.assertEqual(result["case_features_at_cutoff"][0]["state"], "EXCLUDED")
        self.assertEqual(result["base_evidence"][0]["case_feature_annotation_links"][0]["case_state"],
                         "EXCLUDED")
        self.assertEqual(result["future_case_features_excluded"], 1)

    def test_ambiguous_case_feature_time_holds(self):
        document = json.loads((self.root / "assets/case.json").read_text(encoding="utf-8"))
        document["features"].append({"term": "SYNTH-HP:0001", "state": "EXCLUDED",
                                     "recorded_at_utc": "2026-01-08T00:00:00Z"})
        self.replace_case(document)
        self.assertEqual(self.hold_reason(), "AMBIGUOUS_CASE_FEATURE")

    def test_all_case_features_after_cutoff_hold(self):
        document = json.loads((self.root / "assets/case.json").read_text(encoding="utf-8"))
        for index, feature in enumerate(document["features"], start=1):
            feature["recorded_at_utc"] = "2026-02-%02dT00:00:00Z" % index
        self.replace_case(document)
        self.assertEqual(self.hold_reason(), "CASE_WITHOUT_ELIGIBLE_FEATURE")

    def test_future_snapshot_bytes_are_never_opened(self):
        expected = self.result()
        (self.root / "assets/source-a-future.json").unlink()
        self.assertEqual(self.result(), expected)

    def test_cutoff_selects_earlier_snapshot_without_future_backfill(self):
        self.manifest["cutoff_utc"] = "2026-01-09T00:00:00Z"
        self.save_manifest()
        result = self.result()
        self.assertEqual(result["selected_snapshots"][1]["snapshot_id"], "source-a-early")
        self.assertEqual(result["future_snapshots_excluded"], 2)
        self.assertEqual(result["base_evidence"][0]["hpo_annotation_rows"], 1)

    def test_latest_incomplete_snapshot_holds_without_older_fallback(self):
        document = json.loads((self.root / "assets/source-a-current.json").read_text(encoding="utf-8"))
        document["complete"] = False
        self.replace_snapshot(0, 1, document)
        self.assertEqual(self.hold_reason(), "LATEST_SNAPSHOT_INCOMPLETE")

    def test_changed_selected_bytes_hold(self):
        (self.root / "assets/source-a-current.json").write_bytes(b"{}\n")
        self.assertEqual(self.hold_reason(), "PIN_MISMATCH")

    def test_missing_source_eligible_snapshot_holds(self):
        self.manifest["cutoff_utc"] = "2026-01-08T00:00:00Z"
        self.save_manifest()
        self.assertEqual(self.hold_reason(), "SOURCE_WITHOUT_ELIGIBLE_SNAPSHOT")

    def test_future_only_source_holds_without_reading_its_snapshot(self):
        self.manifest["sources"][1]["snapshots"][0]["captured_at_utc"] = "2026-02-03T00:00:00Z"
        self.save_manifest()
        (self.root / "assets/source-b-current.json").unlink()
        self.assertEqual(self.hold_reason(), "SOURCE_WITHOUT_ELIGIBLE_SNAPSHOT")

    def test_observation_recorded_after_cutoff_holds_even_in_selected_snapshot(self):
        document = json.loads((self.root / "assets/source-a-current.json").read_text(encoding="utf-8"))
        document["annotations"][0]["observed_at_utc"] = "2026-01-16T00:00:00Z"
        self.replace_snapshot(0, 1, document)
        self.assertEqual(self.hold_reason(), "OBSERVATION_AFTER_CUTOFF")

    def test_observation_after_snapshot_before_cutoff_holds(self):
        document = json.loads((self.root / "assets/source-a-current.json").read_text(encoding="utf-8"))
        document["annotations"][0]["observed_at_utc"] = "2026-01-11T00:00:00Z"
        self.replace_snapshot(0, 1, document)
        self.assertEqual(self.hold_reason(), "OBSERVATION_AFTER_SNAPSHOT")

    def test_scv_submission_after_cutoff_holds_independently_of_rcv_date(self):
        document = json.loads((self.root / "assets/source-b-current.json").read_text(encoding="utf-8"))
        document["records"][0]["submissions"][1]["observed_at_utc"] = "2026-01-16T00:00:00Z"
        self.replace_snapshot(1, 0, document)
        self.assertEqual(self.hold_reason(), "OBSERVATION_AFTER_CUTOFF")

    def test_real_accession_shapes_are_refused_by_both_source_contracts(self):
        hpo = json.loads((self.root / "assets/source-a-current.json").read_text(encoding="utf-8"))
        hpo["annotations"][0]["term"] = "HP:0000001"
        self.replace_snapshot(0, 1, hpo)
        self.assertEqual(self.hold_reason(), "INVALID_SNAPSHOT")
        hpo = json.loads((SKILL / "assets/source-a-current.json").read_text(encoding="utf-8"))
        self.replace_snapshot(0, 1, hpo)
        clinvar = json.loads((self.root / "assets/source-b-current.json").read_text(encoding="utf-8"))
        clinvar["records"][0]["submissions"][0]["scv"] = "SCV000000001.1"
        self.replace_snapshot(1, 0, clinvar)
        self.assertEqual(self.hold_reason(), "INVALID_SNAPSHOT")

    def test_duplicate_rcv_and_release_mismatch_hold(self):
        clinvar = json.loads((self.root / "assets/source-b-current.json").read_text(encoding="utf-8"))
        clinvar["records"][1]["rcv"] = clinvar["records"][0]["rcv"]
        self.replace_snapshot(1, 0, clinvar)
        self.assertEqual(self.hold_reason(), "DUPLICATE_RCV")
        clinvar = json.loads((SKILL / "assets/source-b-current.json").read_text(encoding="utf-8"))
        self.replace_snapshot(1, 0, clinvar)
        self.manifest["sources"][1]["snapshots"][0]["release"] = "SYNTHETIC-CLINVAR-2"
        self.save_manifest()
        self.assertEqual(self.hold_reason(), "INVALID_SNAPSHOT")

    def test_equal_latest_timestamps_hold_instead_of_order_dependent_selection(self):
        self.manifest["sources"][0]["snapshots"][0]["captured_at_utc"] = "2026-01-10T00:00:00Z"
        self.save_manifest()
        self.assertEqual(self.hold_reason(), "AMBIGUOUS_LATEST_SNAPSHOT")

    def test_duplicate_json_keys_are_rejected_even_when_pinned(self):
        reference = self.manifest["sources"][0]["snapshots"][1]
        raw = b'{"schema":"x","schema":"x"}\n'
        (self.root / reference["path"]).write_bytes(raw)
        reference["sha256"] = hashlib.sha256(raw).hexdigest()
        self.save_manifest()
        self.assertEqual(self.hold_reason(), "INVALID_SNAPSHOT")

    def test_duplicate_or_malformed_manifest_holds_without_counts(self):
        self.manifest_path.write_bytes(b'{"schema":"x","schema":"y"}\n')
        self.assertEqual(self.hold_reason(), "INVALID_MANIFEST")
        self.manifest_path.write_bytes(b'{"schema":')
        self.assertEqual(self.hold_reason(), "INVALID_MANIFEST")

    def test_duplicate_scv_in_selected_clinvar_snapshot_holds(self):
        document = json.loads((self.root / "assets/source-b-current.json").read_text(encoding="utf-8"))
        document["records"][1]["submissions"][0]["scv"] = "SYNTH-SCV:0001.1"
        self.replace_snapshot(1, 0, document)
        self.assertEqual(self.hold_reason(), "DUPLICATE_SCV")

    def test_patient_or_unapproved_fields_and_non_synthetic_case_hold(self):
        document = json.loads((self.root / "assets/case.json").read_text(encoding="utf-8"))
        for forbidden in ("patient_name", "patient_id", "phenotypes", "variants", "free_text"):
            with self.subTest(forbidden=forbidden):
                modified = {**document, forbidden: "not-allowed"}
                raw = (json.dumps(modified) + "\n").encode("utf-8")
                (self.root / "assets/case.json").write_bytes(raw)
                self.manifest["case"]["sha256"] = hashlib.sha256(raw).hexdigest()
                self.save_manifest()
                self.assertEqual(self.hold_reason(), "INVALID_CASE")
        document["synthetic"] = False
        raw = (json.dumps(document) + "\n").encode("utf-8")
        (self.root / "assets/case.json").write_bytes(raw)
        self.manifest["case"]["sha256"] = hashlib.sha256(raw).hexdigest()
        self.save_manifest()
        self.assertEqual(self.hold_reason(), "INVALID_CASE")

    def test_free_text_observation_field_holds(self):
        document = json.loads((self.root / "assets/source-a-current.json").read_text(encoding="utf-8"))
        document["annotations"][0]["note"] = "unapproved free text"
        self.replace_snapshot(0, 1, document)
        self.assertEqual(self.hold_reason(), "INVALID_SNAPSHOT")

    def test_traversal_and_symlink_inputs_are_refused(self):
        self.manifest["case"]["path"] = "../case.json"
        self.save_manifest()
        self.assertEqual(self.hold_reason(), "UNSAFE_PATH")
        self.manifest = json.loads((SKILL / "assets/manifest.json").read_text(encoding="utf-8"))
        self.manifest["case"]["path"] = "assets/case-link.json"
        self.save_manifest()
        link = self.root / "assets/case-link.json"
        try:
            link.symlink_to(self.root / "assets/case.json")
        except (OSError, NotImplementedError):
            original = REPLAY._reparse
            case_inode = (self.root / "assets/case.json").lstat().st_ino
            with mock.patch.object(REPLAY, "_reparse", side_effect=lambda info:
                                   info.st_ino == case_inode or original(info)):
                self.manifest["case"]["path"] = "assets/case.json"
                self.save_manifest()
                self.assertEqual(self.hold_reason(), "UNSAFE_PATH")
            return
        self.assertEqual(self.hold_reason(), "UNSAFE_PATH")

    def test_documented_isolated_cli_writes_new_report_and_preserves_existing(self):
        command = [sys.executable, "-I", "-B", str(SCRIPT), "--root", str(self.root),
                   "--manifest", "assets/manifest.json", "--output", "report.json"]
        first = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(first.returncode, 0, first.stderr)
        report_path = self.root / "report.json"
        original = report_path.read_bytes()
        self.assertEqual(json.loads(first.stdout), json.loads(original))
        second = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(report_path.read_bytes(), original)

    def test_cli_retains_hold_without_evidence_counts(self):
        (self.root / "assets/source-a-current.json").write_bytes(b"{}\n")
        code = REPLAY.main(["--root", str(self.root), "--manifest", "assets/manifest.json",
                            "--output", "hold.json"])
        self.assertEqual(code, 2)
        report = json.loads((self.root / "hold.json").read_text(encoding="utf-8"))
        self.assertEqual((report["status"], report["reason"], report["qualification"]),
                         ("HOLD", "PIN_MISMATCH", "NOT_EVALUATED"))
        self.assertEqual(report["readiness"], "HOLD")
        self.assertNotIn("base_evidence", report)


if __name__ == "__main__":
    unittest.main()
