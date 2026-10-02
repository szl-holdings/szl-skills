"""Offline synthetic contract tests for the paper evidence audit skill."""

import copy
import hashlib
import json
import pathlib
import runpy
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-paper-evidence-audit"
AUDIT = runpy.run_path(str(SKILL / "scripts" / "audit.py"))


class PaperEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.pdf = self.root / "synthetic.pdf"
        self.doc = self.root / "synthetic.docling.json"
        self.claims = self.root / "claims.json"
        self.pdf.write_bytes(b"%PDF-1.7\nsynthetic test bytes\n")
        self.document = {
            "tables": [{"prov": [{"page_no": 2, "bbox": {
                "l": 10, "t": 95, "r": 80, "b": 20, "coord_origin": "BOTTOMLEFT"}}],
                "data": {"table_cells": [
                    {"text": "Dose (mg/L)", "start_row_offset_idx": 0, "start_col_offset_idx": 0},
                    {"text": "12.5 mg/L", "start_row_offset_idx": 1, "start_col_offset_idx": 1}]}}],
            "pictures": [{"prov": [{"page_no": 3, "bbox": {
                "l": 3, "t": 50, "r": 70, "b": 2}}],
                "captions": [{"$ref": "#/texts/0"}]}],
            "texts": [{"text": "Figure 1. Synthetic dose-response panel."}]
        }
        self.manifest = {
            "schema": "szl.paper-evidence-claims.v1",
            "pdf_sha256": hashlib.sha256(self.pdf.read_bytes()).hexdigest(),
            "document_json_sha256": "",
            "extraction": {"tool": "Docling", "version": "test-fixture",
                           "pipeline": "synthetic", "ocr_engine": "none"},
            "claims": [{"id": "table-dose", "ref": "#/tables/0", "quote": "12.5 mg/L"},
                       {"id": "figure-caption", "ref": "#/pictures/0", "quote": "Synthetic dose-response"}]
        }
        self.save()

    def save(self):
        doc_bytes = json.dumps(self.document, sort_keys=True).encode()
        self.doc.write_bytes(doc_bytes)
        self.manifest["document_json_sha256"] = hashlib.sha256(doc_bytes).hexdigest()
        self.claims.write_text(json.dumps(self.manifest), encoding="utf-8")

    def report(self):
        return AUDIT["audit"](self.pdf, self.doc, self.claims)

    def test_table_cell_and_caption_are_located_but_not_verified(self):
        result = self.report()
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertTrue(result["pdf_bound"] and result["document_bound"])
        self.assertFalse(result["scientific_truth_verified"])
        self.assertFalse(result["clinical_use_authorized"])
        self.assertEqual(result["findings"][0]["status"], "TABLE_TEXT_LOCATED_REVIEW_REQUIRED")
        self.assertEqual(result["findings"][0]["match"]["row"], 1)
        self.assertEqual(result["findings"][0]["locators"][0]["page"], 2)
        self.assertEqual(result["findings"][1]["status"], "FIGURE_CAPTION_LOCATED_VISUAL_REVIEW_REQUIRED")
        self.assertEqual(result["findings"][1]["locators"][0]["page"], 3)

    def test_changed_pdf_or_extraction_blocks_all_claims(self):
        for change in ("pdf", "document"):
            with self.subTest(change=change):
                if change == "pdf":
                    self.pdf.write_bytes(b"%PDF-1.7\nchanged\n")
                else:
                    self.document["texts"][0]["text"] += " changed"
                    self.doc.write_text(json.dumps(self.document), encoding="utf-8")
                report = self.report()
                self.assertEqual(report["status"], "UNRESOLVED")
                self.assertTrue(all(f["status"] == "UNRESOLVED" for f in report["findings"]))
                if change == "pdf":
                    self.pdf.write_bytes(b"%PDF-1.7\nsynthetic test bytes\n")
                else:
                    self.save()

    def test_ambiguous_quote_missing_region_and_missing_caption_fail_closed(self):
        original = copy.deepcopy(self.document)
        self.document["tables"][0]["data"]["table_cells"].append({"text": "also 12.5 mg/L"})
        self.save()
        self.assertIn("AMBIGUOUS_QUOTE", self.report()["findings"][0]["issues"])
        self.document = copy.deepcopy(original)
        self.document["tables"][0]["prov"] = []
        self.save()
        self.assertIn("PAGE_REGION_MISSING", self.report()["findings"][0]["issues"])
        self.document = copy.deepcopy(original)
        self.document["pictures"][0]["captions"] = []
        self.save()
        self.assertIn("QUOTE_NOT_LOCATED", self.report()["findings"][1]["issues"])

    def test_missing_item_quote_and_wrong_reference_do_not_pass(self):
        self.manifest["claims"][0]["quote"] = "99 mg/L"
        self.manifest["claims"][1]["ref"] = "#/pictures/99"
        self.save()
        report = self.report()
        self.assertIn("QUOTE_NOT_LOCATED", report["findings"][0]["issues"])
        self.assertIn("SOURCE_ITEM_MISSING", report["findings"][1]["issues"])
        self.manifest["claims"][0]["ref"] = "#/texts/0"
        self.save()
        with self.assertRaisesRegex(ValueError, "INVALID_CLAIM_REF"):
            self.report()

    def test_duplicate_json_keys_duplicate_ids_and_nonfinite_rejected(self):
        self.claims.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "DUPLICATE_JSON_KEY"):
            self.report()
        self.manifest["claims"].append(copy.deepcopy(self.manifest["claims"][0]))
        self.save()
        with self.assertRaisesRegex(ValueError, "DUPLICATE_CLAIM_ID"):
            self.report()
        self.manifest["claims"].pop()
        self.document["tables"][0]["prov"][0]["bbox"]["l"] = float("nan")
        self.save()
        with self.assertRaisesRegex(ValueError, "NONFINITE_JSON"):
            self.report()

    def test_new_output_is_exclusive_and_exit_code_tracks_unresolved(self):
        target = self.root / "report.json"
        args = ["--pdf", str(self.pdf), "--document-json", str(self.doc),
                "--claims", str(self.claims), "--output", str(target)]
        self.assertEqual(AUDIT["main"](args), 0)
        self.assertEqual(AUDIT["main"](args), 1)
        self.manifest["claims"][0]["quote"] = "not there"
        self.save()
        self.assertEqual(AUDIT["main"](args[:-1] + [str(self.root / "unresolved.json")]), 2)


if __name__ == "__main__":
    unittest.main()
