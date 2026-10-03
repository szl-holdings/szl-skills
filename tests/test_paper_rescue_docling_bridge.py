"""Synthetic, offline contract tests for the Docling candidate-record bridge."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "pilots" / "paper-rescue-benchmark" / "szl-paper-rescue-benchmark"
BRIDGE = SKILL / "scripts" / "from_docling.py"
PDF = b"%PDF-1.4\n% Synthetic bytes only; no page pixels.\n%%EOF\n"


def load_bridge():
    # The adapter is a standalone CLI beside its local kernel/scripts. Keep its
    # generic imports isolated when exercising it inside the shared test process.
    local_names = ("kernel", "scripts", "scripts.run")
    retained = {name: sys.modules[name] for name in local_names if name in sys.modules}
    retained_path = sys.path[:]
    for name in local_names:
        sys.modules.pop(name, None)
    try:
        spec = importlib.util.spec_from_file_location("paper_rescue_docling_bridge", BRIDGE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = retained_path
        for name in local_names:
            sys.modules.pop(name, None)
        sys.modules.update(retained)
    return module


bridge = load_bridge()


def box(left, top, right, bottom, origin="TOPLEFT"):
    return {"l": left, "t": top, "r": right, "b": bottom,
            "coord_origin": origin}


def document():
    return {
        "pages": {str(number): {"page_no": number,
                                "size": {"width": 100, "height": 100}}
                  for number in (1, 2)},
        "tables": [{"prov": [{"page_no": 1, "bbox": box(5, 10, 80, 80)}],
                    "data": {"table_cells": [
                        {"text": "12.5 mg/L", "bbox": box(10, 20, 30, 30),
                         "row_header": "Dose", "column_header": "Day 1",
                         "unit": "mg/L"}]}}],
        "pictures": [{"prov": [{"page_no": 2, "bbox": box(0, 0, 100, 100)}],
                      "captions": [{"$ref": "#/texts/0"}]}],
        "texts": [{"text": "Figure 1. Synthetic dose panel.",
                   "prov": [{"page_no": 2,
                             "bbox": box(10, 30, 60, 20, "BOTTOMLEFT")}]}],
    }


def selection(pdf_hash, document_hash):
    return {
        "schema": bridge.SELECTION_SCHEMA,
        "pdf_sha256": pdf_hash,
        "document_json_sha256": document_hash,
        "declared_pages": 2,
        "rights": {"basis": "operator-created", "evidence_uri": "urn:synthetic:bridge",
                   "attestation_id": "synthetic-test", "local_processing_authorized": True,
                   "contains_patient_data": False},
        "pipeline": {"name": "synthetic-docling-json", "version": "fixture-1",
                     "ocr_engine": "none", "remote_services_used": False},
        "items": [{"id": "cellA", "kind": "cell",
                   "ref": "#/tables/0/data/table_cells/0"},
                  {"id": "captionB", "kind": "caption", "ref": "#/pictures/0"}],
    }


class DoclingBridgeTests(unittest.TestCase):
    def test_in_process_loader_preserves_other_kernel_and_script_imports(self):
        local_names = ("kernel", "scripts", "scripts.run")
        retained = {name: sys.modules[name] for name in local_names if name in sys.modules}
        retained_path = sys.path[:]
        sentinels = {name: object() for name in local_names}
        sys.modules.update(sentinels)
        try:
            isolated = load_bridge()
            self.assertEqual(isolated.RECORD_SCHEMA, "szl.paper-rescue.records.v1")
            self.assertEqual(sys.path, retained_path)
            for name, sentinel in sentinels.items():
                self.assertIs(sys.modules[name], sentinel)
        finally:
            for name in local_names:
                sys.modules.pop(name, None)
            sys.modules.update(retained)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.pdf = self.root / "paper.pdf"
        self.document_path = self.root / "paper.docling.json"
        self.selection_path = self.root / "selection.json"
        self.records_path = self.root / "records.json"
        self.provenance_path = self.root / "provenance.json"
        self.pdf.write_bytes(PDF)
        self.document = document()
        self.selection = selection(hashlib.sha256(PDF).hexdigest(), "0" * 64)
        self.save()

    def save(self):
        raw_document = json.dumps(self.document, sort_keys=True).encode("utf-8")
        self.document_path.write_bytes(raw_document)
        self.selection["document_json_sha256"] = hashlib.sha256(raw_document).hexdigest()
        self.selection_path.write_text(json.dumps(self.selection), encoding="utf-8")

    def cli(self):
        return subprocess.run(
            [sys.executable, "-I", "-B", str(BRIDGE), "--pdf", str(self.pdf),
             "--document-json", str(self.document_path), "--selection",
             str(self.selection_path), "--output", str(self.records_path),
             "--provenance-output", str(self.provenance_path)],
            capture_output=True, text=True, check=False)

    def test_located_cell_and_linked_caption_use_own_regions(self):
        completed = self.cli()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        records_bytes = self.records_path.read_bytes()
        records = json.loads(records_bytes)
        provenance = json.loads(self.provenance_path.read_text(encoding="utf-8"))
        self.assertEqual(records["items"][0]["bbox"], [0.1, 0.2, 0.3, 0.3])
        self.assertEqual(records["items"][1]["bbox"], [0.1, 0.7, 0.6, 0.8])
        self.assertEqual(records["items"][0]["text"], "12.5 mg/L")
        self.assertEqual(records["items"][0]["unit"], "mg/L")
        self.assertEqual(provenance["records_json_sha256"],
                         hashlib.sha256(records_bytes).hexdigest())
        self.assertEqual(provenance["document_json_sha256"],
                         hashlib.sha256(self.document_path.read_bytes()).hexdigest())
        self.assertEqual(provenance["selection_json_sha256"],
                         hashlib.sha256(self.selection_path.read_bytes()).hexdigest())
        self.assertEqual(provenance["pdf_sha256"], hashlib.sha256(PDF).hexdigest())
        self.assertEqual(provenance["unresolved_count"], 0)
        self.assertFalse(provenance["docling_pdf_lineage_verified"])
        self.assertFalse(provenance["visual_truth_verified"])
        self.assertNotIn("gold", completed.args)

    def test_nasa_shaped_native_extraction_abstains_on_all_ten_queries(self):
        self.document = {"pages": {str(n): {"page_no": n,
                                            "size": {"width": 100, "height": 100}}
                                   for n in range(1, 14)},
                         "tables": [], "pictures": [
                             {"prov": [{"page_no": n,
                                        "bbox": box(0, 0, 100, 100)}],
                              "captions": []} for n in range(1, 14)],
                         "texts": [{"text": "Synthetic text"} for _ in range(559)]}
        self.selection["declared_pages"] = 13
        self.selection["items"] = ([
            {"id": f"cell{n}", "kind": "cell",
             "ref": f"#/tables/{n}/data/table_cells/0"} for n in range(8)] + [
            {"id": f"caption{n}", "kind": "caption",
             "ref": f"#/pictures/{n}"} for n in (6, 7)])
        self.save()
        completed = self.cli()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        records = json.loads(self.records_path.read_text(encoding="utf-8"))
        self.assertEqual(len(records["items"]), 10)
        self.assertTrue(all(item["status"] == "UNRESOLVED"
                            for item in records["items"]))
        self.assertEqual(json.loads(self.provenance_path.read_text(encoding="utf-8"))[
            "unresolved_count"], 10)

    def test_incomplete_cell_never_borrows_table_box_or_gold_metadata(self):
        for mutation in (
            lambda cell: cell.pop("bbox"),
            lambda cell: cell.pop("row_header"),
            lambda cell: cell.update(column_header=True),
            lambda cell: cell.update(unit=""),
            lambda cell: cell.update(bbox=box(0, 110, 20, 120)),
        ):
            with self.subTest(mutation=mutation):
                changed = document()
                mutation(changed["tables"][0]["data"]["table_cells"][0])
                records = bridge.build_records(self.selection, changed,
                                               self.selection["pdf_sha256"])
                self.assertEqual(records["items"][0]["status"], "UNRESOLVED")
                self.assertEqual(records["items"][1]["status"], "LOCATED")

    def test_missing_coordinate_origin_abstains_instead_of_assuming_top_left(self):
        for path in ("cell", "table", "caption"):
            with self.subTest(path=path):
                changed = document()
                if path == "cell":
                    target = changed["tables"][0]["data"]["table_cells"][0]["bbox"]
                elif path == "table":
                    target = changed["tables"][0]["prov"][0]["bbox"]
                else:
                    target = changed["texts"][0]["prov"][0]["bbox"]
                target.pop("coord_origin")
                records = bridge.build_records(self.selection, changed,
                                               self.selection["pdf_sha256"])
                index = 1 if path == "caption" else 0
                self.assertEqual(records["items"][index]["status"], "UNRESOLVED")

    def test_cell_own_box_and_provenance_box_must_agree(self):
        changed = document()
        cell = changed["tables"][0]["data"]["table_cells"][0]
        cell["prov"] = [{"page_no": 1,
                         "bbox": box(10, 80, 30, 70, "BOTTOMLEFT")}]
        records = bridge.build_records(self.selection, changed,
                                       self.selection["pdf_sha256"])
        self.assertEqual(records["items"][0]["status"], "LOCATED")
        cell["prov"][0]["bbox"] = box(40, 20, 60, 30)
        records = bridge.build_records(self.selection, changed,
                                       self.selection["pdf_sha256"])
        self.assertEqual(records["items"][0]["status"], "UNRESOLVED")
        self.assertEqual(records["items"][0]["reason"], "CELL_REGION_AMBIGUOUS")

    def test_ambiguous_or_cross_page_caption_abstains(self):
        for mutate in (
            lambda doc: doc["pictures"][0].update(captions=[]),
            lambda doc: doc["pictures"][0].update(
                captions=[{"$ref": "#/texts/0"}, {"$ref": "#/texts/0"}]),
            lambda doc: doc["pictures"][0].update(captions=[{"$ref": "#/texts/99"}]),
            lambda doc: doc["pictures"][0].update(
                captions=[{"$ref": "#/texts/0", "cref": "#/texts/1"}]),
            lambda doc: doc["texts"][0]["prov"][0].update(page_no=1),
            lambda doc: doc["texts"][0]["prov"][0].update(
                bbox=box(10, 20, 60, 30, "BOTTOMLEFT")),
        ):
            with self.subTest(mutate=mutate):
                changed = document()
                mutate(changed)
                records = bridge.build_records(self.selection, changed,
                                               self.selection["pdf_sha256"])
                self.assertEqual(records["items"][1]["status"], "UNRESOLVED")

    def test_selection_and_page_contract_fail_closed(self):
        for mutate, code in (
            (lambda selected: selected["items"].append(
                copy.deepcopy(selected["items"][0])), "DUPLICATE_ITEM_ID"),
            (lambda selected: selected["items"][1].update(
                ref=selected["items"][0]["ref"]), "DUPLICATE_SOURCE_REF"),
            (lambda selected: selected["items"][0].update(ref="#/texts/0"),
             "BAD_SOURCE_REF"),
            (lambda selected: selected["rights"].update(
                contains_patient_data=True), "PATIENT_DATA_NOT_ALLOWED"),
            (lambda selected: selected["rights"].update(
                local_processing_authorized=False), "PROCESSING_NOT_AUTHORIZED"),
            (lambda selected: selected["pipeline"].update(
                remote_services_used=True), "REMOTE_PIPELINE_NOT_ALLOWED"),
            (lambda selected: selected.update(declared_pages=3),
             "PAGE_DECLARATION_MISMATCH"),
        ):
            with self.subTest(code=code):
                selected = copy.deepcopy(self.selection)
                mutate(selected)
                with self.assertRaisesRegex(ValueError, code):
                    bridge.build_records(selected, self.document, selected["pdf_sha256"])

    def test_rights_rejected_before_missing_pdf_or_document_is_opened(self):
        self.selection["rights"]["local_processing_authorized"] = False
        self.save()
        self.pdf.unlink()
        self.document_path.unlink()
        completed = self.cli()
        self.assertEqual(completed.returncode, 2)
        self.assertIn("PROCESSING_NOT_AUTHORIZED", completed.stderr)
        self.assertFalse(self.records_path.exists())

    def test_input_hash_drift_and_no_overwrite(self):
        self.pdf.write_bytes(PDF + b"changed")
        completed = self.cli()
        self.assertEqual(completed.returncode, 2)
        self.assertIn("PDF_HASH_MISMATCH", completed.stderr)
        self.pdf.write_bytes(PDF)
        self.document_path.write_bytes(self.document_path.read_bytes() + b" ")
        completed = self.cli()
        self.assertEqual(completed.returncode, 2)
        self.assertIn("DOCUMENT_HASH_MISMATCH", completed.stderr)
        self.save()
        self.assertEqual(self.cli().returncode, 0)
        retained = self.records_path.read_bytes()
        completed = self.cli()
        self.assertEqual(completed.returncode, 2)
        self.assertIn("OUTPUT_ALREADY_EXISTS", completed.stderr)
        self.assertEqual(self.records_path.read_bytes(), retained)

    def test_duplicate_keys_nonfinite_and_size_limit(self):
        self.selection_path.write_text('{"schema":"a","schema":"b"}',
                                       encoding="utf-8")
        self.assertEqual(self.cli().returncode, 2)
        self.assertIn("DUPLICATE_JSON_KEY", self.cli().stderr)
        self.selection_path.write_text('{"float":1e9999}', encoding="utf-8")
        self.assertIn("NONFINITE_NUMBER", self.cli().stderr)
        self.selection_path.write_text('{"integer":' + "9" * 33 + '}',
                                       encoding="utf-8")
        self.assertIn("INTEGER_LIMIT", self.cli().stderr)
        self.selection_path.write_bytes(b"x" * (bridge.SELECTION_LIMIT + 1))
        self.assertIn("JSON_SIZE_LIMIT", self.cli().stderr)
        self.document_path.write_bytes(b"x" * 9)
        with self.assertRaisesRegex(ValueError, "JSON_SIZE_LIMIT"):
            bridge.read_json(self.document_path, 8)

    def test_real_nasa_native_json_if_explicitly_available(self):
        """Optional read-only host witness; CI uses the portable synthetic case."""
        source = os.environ.get("SZL_NASA_DOCLING_JSON")
        if not source:
            self.skipTest("No local NASA Docling JSON path supplied")
        native, _ = bridge.read_json(Path(source), bridge.DOCUMENT_LIMIT)
        sizes = bridge.page_sizes(native, 13)
        selected = ([{"id": f"cell{n}", "kind": "cell",
                      "ref": f"#/tables/{n}/data/table_cells/0"}
                     for n in range(8)] + [
                         {"id": f"caption{n}", "kind": "caption",
                          "ref": f"#/pictures/{n}"} for n in (6, 7)])
        items = [(bridge.selected_cell(native, item, sizes) if item["kind"] == "cell"
                  else bridge.selected_caption(native, item, sizes)) for item in selected]
        self.assertEqual(len(native["texts"]), 559)
        self.assertEqual(len(native["tables"]), 0)
        self.assertEqual(len(items), 10)
        self.assertTrue(all(item["status"] == "UNRESOLVED" for item in items))


if __name__ == "__main__":
    unittest.main()
