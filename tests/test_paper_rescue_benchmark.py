"""Synthetic-only source pilot tests; no PDF interpretation or human gold."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "pilots" / "paper-rescue-benchmark" / "szl-paper-rescue-benchmark"
PDF = b"%PDF-1.4\n% Synthetic hash-binding fixture; no paper image.\n%%EOF\n"
PDF_SHA256 = hashlib.sha256(PDF).hexdigest()


def load_kernel():
    spec = importlib.util.spec_from_file_location("paper_rescue_kernel", SKILL / "kernel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


kernel = load_kernel()


def documents():
    cell = {"id": "cellA", "kind": "cell", "status": "LOCATED", "page": 1,
            "bbox": [0.10, 0.20, 0.30, 0.30], "text": "SYNTHETIC VALUE 12.3",
            "row_header": "Test row", "column_header": "Test column", "unit": "m/s"}
    unresolved = {"id": "captionB", "kind": "caption", "status": "UNRESOLVED",
                  "reason": "No linked caption in synthetic input"}
    records = {"schema": kernel.RECORD_SCHEMA, "pdf_sha256": PDF_SHA256,
               "declared_pages": 1,
               "rights": {"basis": "operator-created", "evidence_uri": "urn:synthetic:paper-rescue",
                          "attestation_id": "synthetic-test", "local_processing_authorized": True,
                          "contains_patient_data": False},
               "pipeline": {"name": "synthetic-fixture", "version": "1", "ocr_engine": "none",
                            "remote_services_used": False},
               "items": [cell, unresolved]}
    gold = {"schema": kernel.GOLD_SCHEMA, "pdf_sha256": PDF_SHA256,
            "declared_pages": 1, "annotation_basis": "SYNTHETIC_FIXTURE",
            "reviewer_count": 0, "items": [copy.deepcopy(cell), copy.deepcopy(unresolved)]}
    return records, gold


def cli_run(records, gold, output=False, raw_records=None, pdf=PDF):
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        pdf_path = base / "fixture.pdf"
        records_path = base / "records.json"
        gold_path = base / "gold.json"
        pdf_path.write_bytes(pdf)
        records_path.write_bytes(raw_records if raw_records is not None else
                                 json.dumps(records).encode("utf-8"))
        gold_path.write_text(json.dumps(gold), encoding="utf-8")
        command = [sys.executable, "-I", "-B", str(SKILL / "scripts" / "run.py"),
                   "--pdf", str(pdf_path), "--records", str(records_path),
                   "--gold", str(gold_path)]
        report_path = base / "report.json" if output else None
        if report_path is not None:
            command += ["--output", str(report_path)]
        first = subprocess.run(command, capture_output=True, text=True, check=False)
        rendered = (report_path.read_text(encoding="utf-8") if
                    report_path is not None and report_path.exists() else first.stdout)
        second = (subprocess.run(command, capture_output=True, text=True, check=False)
                  if report_path is not None else None)
        retained = report_path.read_text(encoding="utf-8") if report_path is not None else None
        return first, json.loads(rendered), second, retained


class PaperRescuePilotTests(unittest.TestCase):
    def test_unresolved_stays_in_primary_denominator(self):
        records, gold = documents()
        report = kernel.score(records, gold, PDF_SHA256)
        self.assertEqual(report["status"], "SCORED_REVIEW_REQUIRED")
        self.assertEqual(report["denominator"], 2)
        self.assertEqual(report["counts"]["matched_reference"], 1)
        self.assertEqual(report["counts"]["abstained"], 1)
        self.assertFalse(report["visual_truth_verified"])
        self.assertFalse(report["rights_verified"])
        self.assertFalse(report["pipeline_remote_services_verified"])

    def test_missing_candidate_stays_in_denominator(self):
        records, gold = documents()
        records["items"].pop()
        report = kernel.score(records, gold, PDF_SHA256)
        self.assertEqual(report["denominator"], 2)
        self.assertEqual(report["counts"]["missing"], 1)

    def test_empty_candidates_score_as_complete_miss(self):
        records, gold = documents()
        records["items"] = []
        report = kernel.score(records, gold, PDF_SHA256)
        self.assertEqual(report["status"], "SCORED_REVIEW_REQUIRED")
        self.assertEqual(report["denominator"], 2)
        self.assertEqual(report["counts"]["missing"], 2)
        self.assertEqual(report["counts"]["matched_reference"], 0)
        self.assertEqual(report["findings"], [
            {"id": "cellA", "result": "MISSING"},
            {"id": "captionB", "result": "MISSING"},
        ])
        self.assertFalse(report["visual_truth_verified"])
        self.assertFalse(report["rights_verified"])
        completed, cli_report, _, _ = cli_run(records, gold)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(cli_report["counts"]["missing"], 2)
        self.assertEqual(cli_report["denominator"], 2)

        gold["items"] = []
        with self.assertRaisesRegex(ValueError, "^BAD_ITEM_COUNT$"):
            kernel.score(records, gold, PDF_SHA256)

    def test_wrong_reference_locator_and_content_are_not_matches(self):
        records, gold = documents()
        records["items"][0]["bbox"] = [0.65, 0.65, 0.75, 0.75]
        report = kernel.score(records, gold, PDF_SHA256)
        self.assertEqual(report["status"], "REFERENCE_MISMATCH")
        self.assertEqual(report["counts"]["reference_locator_mismatch"], 1)
        records, gold = documents()
        records["items"][0]["unit"] = "km/s"
        report = kernel.score(records, gold, PDF_SHA256)
        self.assertEqual(report["counts"]["reference_content_mismatch"], 1)

    def test_located_claim_against_negative_reference_fails(self):
        records, gold = documents()
        records["items"][1] = {"id": "captionB", "kind": "caption", "status": "LOCATED",
                               "page": 1, "bbox": [0.4, 0.4, 0.6, 0.5],
                               "text": "Invented caption"}
        report = kernel.score(records, gold, PDF_SHA256)
        self.assertEqual(report["counts"]["unsupported_located_claim"], 1)
        self.assertEqual(report["status"], "REFERENCE_MISMATCH")

    def test_hash_rights_remote_and_patient_tampering_fail_closed(self):
        for change, code in (
            (lambda r, g: r.update(pdf_sha256="0" * 64), "PDF_HASH_MISMATCH"),
            (lambda r, g: r["rights"].update(local_processing_authorized=False),
             "PROCESSING_NOT_AUTHORIZED"),
            (lambda r, g: r["rights"].update(contains_patient_data=True),
             "PATIENT_DATA_NOT_ALLOWED"),
            (lambda r, g: r["pipeline"].update(remote_services_used=True),
             "REMOTE_PIPELINE_NOT_ALLOWED"),
            (lambda r, g: g.update(pdf_sha256="0" * 64), "GOLD_PDF_HASH_MISMATCH"),
        ):
            with self.subTest(code=code):
                records, gold = documents()
                change(records, gold)
                with self.assertRaises(ValueError) as caught:
                    kernel.score(records, gold, PDF_SHA256)
                self.assertEqual(str(caught.exception), code)

    def test_malformed_rights_basis_fails_as_invalid_input(self):
        for basis in ([], {}, "not-a-rights-basis"):
            with self.subTest(basis=basis):
                records, gold = documents()
                records["rights"]["basis"] = basis
                with self.assertRaises(ValueError) as caught:
                    kernel.score(records, gold, PDF_SHA256)
                self.assertEqual(str(caught.exception), "RIGHTS_BASIS_MISSING")
                completed, report, _, _ = cli_run(records, gold)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertEqual(report["status"], "INVALID_INPUT")
                self.assertEqual(report["error_code"], "RIGHTS_BASIS_MISSING")

    def test_bbox_page_and_partial_unresolved_fail_closed(self):
        for mutation, code in (
            (lambda r: r["items"][0].update(page=2), "PAGE_OUT_OF_DECLARED_RANGE"),
            (lambda r: r["items"][0].update(bbox=[0.1, 0.2, 1.1, 0.3]), "BAD_BBOX"),
            (lambda r: r["items"][1].update(page=1), "INVALID_FIELDS"),
            (lambda r: r["items"].append(copy.deepcopy(r["items"][0])),
             "DUPLICATE_ITEM_ID"),
        ):
            with self.subTest(code=code):
                records, gold = documents()
                mutation(records)
                with self.assertRaises(ValueError) as caught:
                    kernel.score(records, gold, PDF_SHA256)
                self.assertEqual(str(caught.exception), code)

    def test_gold_role_claim_and_extra_candidate_rejected(self):
        records, gold = documents()
        gold.update(annotation_basis="INDEPENDENT_HUMAN_ADJUDICATED", reviewer_count=1)
        with self.assertRaisesRegex(ValueError, "BAD_REVIEWER_COUNT"):
            kernel.score(records, gold, PDF_SHA256)
        records, gold = documents()
        records["items"].append({"id": "unregistered", "kind": "cell", "status": "UNRESOLVED",
                                 "reason": "Not in selected reference"})
        with self.assertRaisesRegex(ValueError, "UNREGISTERED_CANDIDATE_ID"):
            kernel.score(records, gold, PDF_SHA256)

    def test_cli_emits_bound_report_without_source_text(self):
        records, gold = documents()
        completed, report, _, _ = cli_run(records, gold)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(report["status"], "SCORED_REVIEW_REQUIRED")
        self.assertEqual(report["pdf_sha256"], PDF_SHA256)
        self.assertIn("records_bytes_sha256", report)
        self.assertIn("gold_bytes_sha256", report)
        self.assertNotIn("SYNTHETIC VALUE", completed.stdout)

    def test_cli_duplicate_json_key_and_non_pdf_rejected(self):
        records, gold = documents()
        raw = json.dumps(records).replace('"schema":', '"schema":"wrong", "schema":', 1).encode()
        completed, report, _, _ = cli_run(records, gold, raw_records=raw)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(report["error_code"], "DUPLICATE_JSON_KEY")
        completed, report, _, _ = cli_run(records, gold, pdf=b"not a PDF")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(report["error_code"], "NOT_A_PDF_HEADER")

    def test_cli_rejects_rights_before_opening_pdf(self):
        records, gold = documents()
        records["rights"]["local_processing_authorized"] = False
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            records_path = base / "records.json"
            gold_path = base / "gold.json"
            records_path.write_text(json.dumps(records), encoding="utf-8")
            gold_path.write_text(json.dumps(gold), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-I", "-B", str(SKILL / "scripts" / "run.py"),
                 "--pdf", str(base / "missing.pdf"), "--records", str(records_path),
                 "--gold", str(gold_path)], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)["error_code"], "PROCESSING_NOT_AUTHORIZED")

    def test_cli_mismatch_exit_one_and_exclusive_output(self):
        records, gold = documents()
        records["items"][0]["text"] = "wrong"
        completed, report, second, retained = cli_run(records, gold, output=True)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(report["status"], "REFERENCE_MISMATCH")
        self.assertEqual(second.returncode, 2)
        self.assertEqual(json.loads(retained), report)


if __name__ == "__main__":
    unittest.main()
