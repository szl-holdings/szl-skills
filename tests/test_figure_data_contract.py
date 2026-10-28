"""Actual mapping replay and corruption rejection, without services or weights."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys

SKILL = Path(__file__).resolve().parents[1] / "skills" / "szl-figure-data-contract"
SPEC = importlib.util.spec_from_file_location("figure_contract", SKILL / "scripts" / "run.py")
RUN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUN)

# ChEMBL-shaped three-row case from an outside reproduction: c3 is censored and in uM.
CENSORED_CSV = ("id,dose,standard_relation,standard_value,standard_units\n"
                "c1,1,=,50,nM\nc2,2,=,120,nM\nc3,3,>,10000,uM\n")
EXACT_CSV = CENSORED_CSV.replace("c3,3,>,10000,uM", "c3,3,=,10000,nM")
DECLARED = {"relation_column": "standard_relation", "unit_column": "standard_units"}


class FigureContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "assets").mkdir()
        for name in ("data.csv", "spec.json"):
            (self.root / "assets" / name).write_bytes((SKILL / "assets" / name).read_bytes())

    def render(self):
        return RUN.render(self.root, "assets/spec.json", "bundle")

    def change_spec(self, edit):
        path = self.root / "assets" / "spec.json"
        spec = json.loads(path.read_text())
        edit(spec)
        path.write_bytes(RUN.encoded(spec))

    def remint_outputs(self):
        path = self.root / "bundle" / "receipt.json"
        receipt = json.loads(path.read_text())
        receipt.pop("receipt_sha256")
        for name in receipt["outputs"]:
            receipt["outputs"][name] = RUN.digest((self.root / "bundle" / name).read_bytes())
        receipt["receipt_sha256"] = RUN.digest(RUN.encoded(receipt))
        path.write_bytes(RUN.encoded(receipt))

    def assert_mismatch(self):
        with self.assertRaises(RUN.ContractError) as caught:
            RUN.verify(self.root, "bundle")
        self.assertEqual(caught.exception.status, "MISMATCH")

    def test_scatter_replays_and_refuses_overwrite(self):
        self.assertEqual(self.render()["points"], 3)
        self.assertEqual(RUN.verify(self.root, "bundle")["status"], "MATCH")
        with self.assertRaises(RUN.ContractError):
            self.render()

    def test_line_preserves_order(self):
        self.change_spec(lambda s: s.update(kind="line"))
        self.render()
        self.assertEqual(RUN.verify(self.root, "bundle")["status"], "MATCH")
        svg = (self.root / "bundle" / "figure.svg").read_text()
        self.assertIn('points="60.000000,400.000000 330.000000,130.000000 600.000000,40.000000"', svg)

    def test_render_deterministic(self):
        self.render()
        RUN.render(self.root, "assets/spec.json", "second")
        for name in ("figure.svg", "plotted-data.json", "receipt.json"):
            self.assertEqual((self.root / "bundle" / name).read_bytes(), (self.root / "second" / name).read_bytes())

    def test_false_caption_refused_before_write(self):
        self.change_spec(lambda s: s["caption"].update(n=4))
        with self.assertRaises(RUN.ContractError) as caught:
            self.render()
        self.assertEqual(caught.exception.status, "MISMATCH")
        self.assertFalse((self.root / "bundle").exists())

    def test_xml_invalid_axis_text_refused_before_write(self):
        for value in ("\ufffe", "\uffff", "\ud800", "\x00"):
            with self.subTest(value=repr(value)):
                path = self.root / "assets" / "spec.json"
                spec = json.loads(path.read_text(encoding="utf-8"))
                spec["x"]["unit"] = value
                path.write_bytes(json.dumps(spec, ensure_ascii=True).encode("utf-8"))
                with self.assertRaises(RUN.ContractError):
                    self.render()
                self.assertFalse((self.root / "bundle").exists())

    def test_unsupported_evidence_claims_in_reminted_receipt_refused(self):
        self.render()
        path = self.root / "bundle" / "receipt.json"
        original = path.read_bytes()
        for field in ("third_party_witness", "scientific_truth", "production_approved"):
            with self.subTest(field=field):
                receipt = json.loads(original)
                receipt.pop("receipt_sha256")
                receipt[field] = True
                receipt["receipt_sha256"] = RUN.digest(RUN.encoded(receipt))
                path.write_bytes(RUN.encoded(receipt))
                self.assert_mismatch()

    def test_input_byte_change_detected(self):
        self.render()
        path = self.root / "assets" / "data.csv"
        path.write_text(path.read_text().replace("r1,1,2", "r1,1,2.0"))
        self.assert_mismatch()

    def test_malformed_but_readable_changed_inputs_are_mismatches(self):
        self.render()
        for name, raw in (("spec.json", b"\xff"), ("data.csv", b"\xff"),
                          ("spec.json", b'{"schema":'), ("data.csv", b'id,dose,response\nr1,broken\n')):
            path = self.root / "assets" / name
            original = path.read_bytes()
            with self.subTest(name=name, raw=raw):
                path.write_bytes(raw)
                self.assert_mismatch()
                path.write_bytes(original)

    def test_semantic_svg_corruptions_after_receipt_remint(self):
        self.render()
        path = self.root / "bundle" / "figure.svg"
        original = path.read_text()
        variants = [
            original.replace('<circle data-row="r1" cx="60.000000" cy="400.000000" r="3" fill="#1764ab"/>\n', ""),
            original.replace('data-row="r1"', 'data-row="r2"'),
            original.replace('cx="330.000000"', 'cx="331.000000"'),
            original.replace('dose (mg)', 'dose (g)'),
            original.replace('n=3;', 'n=4;'),
            original.replace('</svg>', '<script>alert(1)</script></svg>'),
            '<?xml-stylesheet type="text/css" href="https://example.invalid/style.css"?>\n' + original,
            original.replace('</g>', 'extra visible text</g>'),
            original.replace('</svg>', ''),
            original.replace('<svg ', '<svg\n'),
        ]
        for variant in variants:
            with self.subTest(corruption=variant[-120:]):
                path.write_text(variant, encoding="utf-8")
                self.remint_outputs()
                self.assert_mismatch()

    def test_plotted_sidecar_corruption_after_receipt_remint(self):
        self.render()
        path = self.root / "bundle" / "plotted-data.json"
        data = json.loads(path.read_text())
        data["points"][0]["y"] = "99"
        path.write_bytes(RUN.encoded(data))
        self.remint_outputs()
        self.assert_mismatch()

    def test_missing_artifact_unavailable(self):
        self.render()
        with self.assertRaises(FileNotFoundError):
            RUN.verify(self.root, "missing")

    def test_traversal_absolute_and_noncanonical_paths(self):
        for path in (".", "../outside", "/outside", "a//b", "a/../b", "C:/outside", "a\\b", ".env"):
            with self.subTest(path=path), self.assertRaises(RUN.ContractError):
                RUN.safe_path(self.root, path)

    def test_duplicate_ids_nonfinite_and_csv_width(self):
        path = self.root / "assets" / "data.csv"
        for raw in ("id,dose,response\nr1,1,2\nr1,2,3\n", "id,dose,response\nr1,NaN,2\n", "id,dose,response\nr1,1,2,extra\n"):
            path.write_text(raw)
            with self.subTest(raw=raw), self.assertRaises(RUN.ContractError):
                self.render()

    def test_oversized_csv(self):
        (self.root / "assets" / "data.csv").write_bytes(b"x" * (RUN.MAX_BYTES + 1))
        with self.assertRaises(RUN.ContractError):
            self.render()

    def test_duplicate_json_keys(self):
        with self.assertRaises(RUN.ContractError):
            RUN.read_json(b'{"x":1,"x":2}')

    def test_cli_deep_json_is_unknown_without_traceback(self):
        (self.root / "assets" / "spec.json").write_bytes(b'[' * 2000 + b'0' + b']' * 2000)
        result = subprocess.run([sys.executable, "-I", "-B", str(SKILL / "scripts" / "run.py"),
                                 "render", "assets/spec.json", "--root", str(self.root), "--output", "bundle"],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "UNKNOWN")
        self.assertEqual(result.stderr, "")
        self.assertFalse((self.root / "bundle").exists())

    def test_extreme_decimal_exponents_and_precision_refused(self):
        for value in ("1e999999999999999999", "1e101", "1e-101", "12345678901234567890123456789012345", "Infinity"):
            with self.subTest(value=value), self.assertRaises(RUN.ContractError):
                RUN.number(value)

    def test_symlink_refused_when_supported(self):
        link = self.root / "linked.csv"
        try:
            link.symlink_to(self.root / "assets" / "data.csv")
        except OSError:
            self.skipTest("symlink privileges unavailable")
        with self.assertRaises(RUN.ContractError):
            RUN.safe_path(self.root, "linked.csv")

    def use_assay(self, csv_text, **y_extra):
        (self.root / "assets" / "data.csv").write_bytes(csv_text.encode("utf-8"))
        self.change_spec(lambda s: s.update(
            y={"column": "standard_value", "unit": "nM", **y_extra},
            caption={"n": 3, "x_min": "1", "x_max": "3", "y_min": "50", "y_max": "10000"}))

    def assert_unknown_before_write(self):
        with self.assertRaises(RUN.ContractError) as caught:
            self.render()
        self.assertEqual(caught.exception.status, "UNKNOWN")
        self.assertFalse((self.root / "bundle").exists())

    def test_undeclared_relation_and_unit_columns_are_unknown(self):
        for csv_text in (CENSORED_CSV, EXACT_CSV):
            with self.subTest(csv=csv_text):
                self.use_assay(csv_text)
                self.assert_unknown_before_write()
        result = subprocess.run([sys.executable, "-I", "-B", str(SKILL / "scripts" / "run.py"),
                                 "render", "assets/spec.json", "--root", str(self.root), "--output", "bundle"],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "UNKNOWN")

    def test_censored_relation_is_unknown_not_an_exact_point(self):
        self.use_assay(CENSORED_CSV.replace(",uM", ",nM"), **DECLARED)
        self.assert_unknown_before_write()

    def test_unit_disagreeing_with_axis_is_unknown(self):
        self.use_assay(CENSORED_CSV.replace(",>,", ",=,"), **DECLARED)
        self.assert_unknown_before_write()

    def test_relation_and_unit_variants_are_unknown(self):
        for relation, unit in (("<", "nM"), (">=", "nM"), ("~", "nM"), ("", "nM"), ("'='", "nM"),
                               ("= ", "nM"), ("=", "NM"), ("=", "")):
            with self.subTest(relation=relation, unit=unit):
                self.use_assay(EXACT_CSV.replace("c3,3,=,10000,nM", f"c3,3,{relation},10000,{unit}"), **DECLARED)
                self.assert_unknown_before_write()

    def test_optional_axis_columns_are_bounded(self):
        for extra in ({"scale": "log"}, {"relation_column": "missing"}, {"relation_column": "dose"},
                      {"relation_column": "standard_units", "unit_column": "standard_units"}):
            with self.subTest(extra=extra):
                self.use_assay(EXACT_CSV, **extra)
                with self.assertRaises(RUN.ContractError):
                    self.render()
                self.assertFalse((self.root / "bundle").exists())

    def test_censored_csv_behind_reminted_receipt_is_never_match(self):
        # Same id/x/y values as the censored rows, then rebind the receipt to censored bytes.
        self.use_assay("id,dose,standard_value\nc1,1,50\nc2,2,120\nc3,3,10000\n")
        self.render()
        (self.root / "assets" / "data.csv").write_bytes(CENSORED_CSV.encode("utf-8"))
        path = self.root / "bundle" / "receipt.json"
        receipt = json.loads(path.read_text())
        receipt.pop("receipt_sha256")
        receipt["data_sha256"] = RUN.digest((self.root / "assets" / "data.csv").read_bytes())
        receipt["receipt_sha256"] = RUN.digest(RUN.encoded(receipt))
        path.write_bytes(RUN.encoded(receipt))
        with self.assertRaises(RUN.ContractError) as caught:
            RUN.verify(self.root, "bundle")
        self.assertEqual(caught.exception.status, "UNKNOWN")

    def test_exact_rows_match_with_fixed_notation(self):
        for index, spelling in enumerate(("10000", "1e4", "1E+4", "10000.0")):
            with self.subTest(spelling=spelling):
                self.use_assay(EXACT_CSV.replace(",10000,", "," + spelling + ","), **DECLARED)
                RUN.render(self.root, "assets/spec.json", f"bundle{index}")
                self.assertEqual(RUN.verify(self.root, f"bundle{index}")["status"], "MATCH")
                svg = (self.root / f"bundle{index}" / "figure.svg").read_text(encoding="utf-8")
                self.assertIn("n=3; x=[1, 3]; y=[50, 10000]", svg)
                self.assertNotRegex(svg, r"[0-9][eE][+-]?[0-9]")
                plotted = json.loads((self.root / f"bundle{index}" / "plotted-data.json").read_text(encoding="utf-8"))
                self.assertEqual([p["y"] for p in plotted["points"]], ["50", "120", "10000"])
                self.assertEqual(plotted["summary"]["y_max"], "10000")
                self.assertEqual(plotted["y"], {"column": "standard_value", "unit": "nM", **DECLARED})


if __name__ == "__main__":
    unittest.main()
