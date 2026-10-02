"""Adversarial checks for the bounded synthetic-only evidence map."""

import copy
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SKILL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))
from kernel import EvidenceMapError, map_evidence, read_input

ASSETS = SKILL / "assets"


def fixture():
    manifest_raw = read_input(ASSETS / "example-manifest.json")
    hpo_raw = read_input(ASSETS / "synthetic-hpo.json")
    clinvar_raw = read_input(ASSETS / "synthetic-clinvar.json")
    return (json.loads(manifest_raw), json.loads(hpo_raw), json.loads(clinvar_raw),
            manifest_raw, hpo_raw, clinvar_raw)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


class EvidenceMapTests(unittest.TestCase):
    def setUp(self):
        self.manifest, self.hpo, self.clinvar, self.manifest_raw, self.hpo_raw, self.clinvar_raw = fixture()

    def build(self, manifest=None, hpo=None, clinvar=None):
        hpo_raw = self.hpo_raw if hpo is None else encoded(hpo)
        clinvar_raw = self.clinvar_raw if clinvar is None else encoded(clinvar)
        if manifest is None and (hpo is not None or clinvar is not None):
            manifest = copy.deepcopy(self.manifest)
            manifest["sources"]["hpo"]["sha256"] = hashlib.sha256(hpo_raw).hexdigest()
            manifest["sources"]["clinvar"]["sha256"] = hashlib.sha256(clinvar_raw).hexdigest()
        manifest_raw = self.manifest_raw if manifest is None else encoded(manifest)
        return map_evidence(manifest_raw, hpo_raw, clinvar_raw)

    def test_cli_retains_versions_disagreement_gaps_and_hold(self):
        completed = subprocess.run(
            [sys.executable, "-B", str(SKILL / "scripts" / "run.py"),
             str(ASSETS / "example-manifest.json"), str(ASSETS / "synthetic-hpo.json"),
             str(ASSETS / "synthetic-clinvar.json")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["readiness"], "HOLD")
        self.assertEqual(report["sources"]["hpo"]["binding"], "EXACT_INPUT_BYTES")
        self.assertEqual(report["sources"]["clinvar"]["release"], "SYNTHETIC-CLINVAR-1")
        self.assertEqual([row["condition"] for row in report["conditions"]],
                         ["SYNTH-COND:0001", "SYNTH-COND:0002", "SYNTH-COND:0003"])
        first = report["conditions"][0]
        self.assertIn("DIVERGENT_DECLARED_TOKENS", first["review_flags"])
        self.assertEqual(first["hpo_annotations"][0]["term"], "SYNTH-HP:0001")
        self.assertEqual(first["clinvar_records"][0]["rcv"], "SYNTH-RCV:0001.1")
        self.assertEqual(first["clinvar_records"][0]["submissions"][1]["scv"], "SYNTH-SCV:0002.2")
        self.assertEqual(report["conditions"][1]["review_flags"], ["MISSING_CLINVAR_RECORD"])
        self.assertEqual(report["conditions"][2]["review_flags"], ["MISSING_HPO_ANNOTATION"])

    def test_byte_tamper_is_hold_and_cli_emits_no_report(self):
        with tempfile.TemporaryDirectory() as folder:
            changed = pathlib.Path(folder) / "hpo.json"
            changed.write_bytes((ASSETS / "synthetic-hpo.json").read_bytes() + b" ")
            completed = subprocess.run(
                [sys.executable, "-B", str(SKILL / "scripts" / "run.py"),
                 str(ASSETS / "example-manifest.json"), str(changed),
                 str(ASSETS / "synthetic-clinvar.json")],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("binding mismatch", completed.stderr)

    def test_duplicate_key_and_nonfinite_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "input.json"
            path.write_text('{"synthetic":true,"synthetic":true}', encoding="utf-8")
            with self.assertRaisesRegex(EvidenceMapError, "duplicate JSON key"):
                read_input(path)
            path.write_text('{"value":NaN}', encoding="utf-8")
            with self.assertRaisesRegex(EvidenceMapError, "non-finite"):
                read_input(path)

    def test_integer_digit_and_nesting_limits_exit_cleanly(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "hostile.json"
            for payload in (b"9" * 5000, b"[" * 1200 + b"0" + b"]" * 1200):
                path.write_bytes(payload)
                with self.assertRaises(EvidenceMapError):
                    read_input(path)
                completed = subprocess.run(
                    [sys.executable, "-B", str(SKILL / "scripts" / "run.py"),
                     str(path), str(ASSETS / "synthetic-hpo.json"),
                     str(ASSETS / "synthetic-clinvar.json")],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertNotIn("Traceback", completed.stderr)

    def test_real_accessions_and_patient_fields_are_rejected(self):
        hpo = copy.deepcopy(self.hpo)
        hpo["annotations"][0]["term"] = "HP:0001250"
        with self.assertRaisesRegex(EvidenceMapError, "not a synthetic token"):
            self.build(hpo=hpo)
        manifest = copy.deepcopy(self.manifest)
        manifest["patient_id"] = "person-1"
        with self.assertRaisesRegex(EvidenceMapError, "unknown fields"):
            self.build(manifest=manifest)
        clinvar = copy.deepcopy(self.clinvar)
        clinvar["records"][0]["submissions"][0]["scv"] = "SCV000000001.1"
        with self.assertRaisesRegex(EvidenceMapError, "not a synthetic token"):
            self.build(clinvar=clinvar)

    def test_synthetic_flag_and_rights_are_required(self):
        for key, value in (("synthetic", False), ("rights", "UNKNOWN")):
            hpo = copy.deepcopy(self.hpo)
            hpo[key] = value
            with self.assertRaisesRegex(EvidenceMapError, "synthetic-only"):
                self.build(hpo=hpo)

    def test_opposing_hpo_qualifiers_remain_visible(self):
        hpo = copy.deepcopy(self.hpo)
        hpo["annotations"].append({
            "condition": "SYNTH-COND:0001", "term": "SYNTH-HP:0001",
            "qualifier": "EXPLICIT_NEGATIVE_ANNOTATION", "source_ref": "SYNTH-REF:STUDY-D",
        })
        report = self.build(hpo=hpo)
        first = report["conditions"][0]
        self.assertIn("DIVERGENT_HPO_QUALIFIERS", first["review_flags"])
        self.assertEqual(len(first["hpo_annotations"]), 3)

    def test_duplicate_submitted_record_is_rejected(self):
        clinvar = copy.deepcopy(self.clinvar)
        clinvar["records"][1]["submissions"][0]["scv"] = "SYNTH-SCV:0001.1"
        with self.assertRaisesRegex(EvidenceMapError, "duplicate SCV"):
            self.build(clinvar=clinvar)

    def test_unsupported_free_text_and_missing_binding_are_rejected(self):
        clinvar = copy.deepcopy(self.clinvar)
        clinvar["records"][0]["submissions"][0]["assertion_token"] = "patient has disease"
        with self.assertRaisesRegex(EvidenceMapError, "not a synthetic token"):
            self.build(clinvar=clinvar)
        manifest = copy.deepcopy(self.manifest)
        manifest["sources"]["clinvar"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(EvidenceMapError, "binding mismatch"):
            self.build(manifest=manifest)

    def test_importable_api_hashes_raw_bytes_and_rejects_clinical_token(self):
        hpo = copy.deepcopy(self.hpo)
        hpo["annotations"][0]["term"] = "SYNTH-HP:9999"
        with self.assertRaisesRegex(EvidenceMapError, "binding mismatch"):
            map_evidence(self.manifest_raw, encoded(hpo), self.clinvar_raw)
        with self.assertRaisesRegex(EvidenceMapError, "bounded raw JSON bytes"):
            map_evidence(self.manifest, self.hpo_raw, self.clinvar_raw)
        clinvar = copy.deepcopy(self.clinvar)
        clinvar["records"][0]["submissions"][0]["assertion_token"] = "SYNTH-ASSERT:PATHOGENIC"
        with self.assertRaisesRegex(EvidenceMapError, "not a synthetic token"):
            self.build(clinvar=clinvar)

    def test_input_size_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "oversize.json"
            path.write_bytes(b" " * (256 * 1024 + 1))
            with self.assertRaisesRegex(EvidenceMapError, "exceeds 256 KiB"):
                read_input(path)


if __name__ == "__main__":
    unittest.main()
