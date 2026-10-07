"""Synthetic, offline tests for the separately pinned v2 experiment replay operations."""
# SPDX-License-Identifier: Apache-2.0

import ast
import contextlib
import hashlib
import io
import json
import pathlib
import runpy
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from fractions import Fraction
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-experiment-replay"
OPS = runpy.run_path(str(SKILL / "scripts" / "engine_ops.py"))
ENGINE = runpy.run_path(str(SKILL / "scripts" / "engine.py"))
CLI = runpy.run_path(str(SKILL / "scripts" / "run.py"))

# Computed from origin/main b8e259c (scripts/engine.py and scripts/run.py unchanged there) by running
# `run.py prepare assets/declaration.json` then `run.py replay` on a copy of the shipped assets.
MAIN_ENGINE_SHA256 = "40b65dcb7f48de5655aca6e14f900d95dfd17121c02b5ab4f866361a352cf281"
MAIN_V1_PIN_SHA256 = "7a7a024488e07d94246d28acbd9be756e33ba115bbf0d9ea9ede78ac74a2638d"
MAIN_V1_RECEIPT_SHA256 = "f4bd1291b19fea83fcd662ae5fa96fba2a33d0cc08a057b30c7ac59a52df5ff5"
MAIN_V1_PIN_FILE_SHA256 = "c7d5c2c36a5bc8bd9506e3f6f8fe58096e4a8ab898d3076ba2367a4173ebd6d2"
MAIN_V1_RECEIPT_FILE_SHA256 = "6592cec3d77d8d3706a2fcfae7793362969c9164ed51b04ebccbf2ee88e693d7"

DATA = "sample,arm,measurement\na,x,2.0\nb,x,3.0\nc,y,1.5\nd,y,4\ne,y,0.5\n"
# Exact expectations for DATA: sum 11, count 5, median 2, sample variance 7.3 / 4, means x 2.5 and y 2.
EXPECTED = {
    "csv_column_sum_v1": {"measurement": "11"},
    "csv_column_count_v1": {"measurement": "5"},
    "csv_column_median_v1": {"measurement": "2"},
    "csv_column_variance_v1": {"measurement": "1.825"},
    "csv_grouped_mean_v1": {"x": "2.5", "y": "2"},
}
SHIPPED = ("synthetic.csv", "expected.json", "declaration.json", "ops-declaration.json", "ops-expected.json",
           "ops-unsupported.json")


def quiet(argv):
    with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
        code = CLI["main"](argv)
    return code, stdout.getvalue()


class ExperimentReplayOpsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="szl-experiment-replay-ops-")
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        (self.root / "assets").mkdir()
        for name in SHIPPED:
            (self.root / "assets" / name).write_bytes((SKILL / "assets" / name).read_bytes())
        (self.root / "data.csv").write_text(DATA, encoding="utf-8", newline="")

    def declare(self, operation, values, tolerance="0", name="ref.json", data="data.csv", **extra):
        reference = {"schema": OPS["REFERENCE_SCHEMA"], "operation": operation, "values": values}
        (self.root / name).write_text(json.dumps(reference), encoding="utf-8")
        declaration = {"schema": OPS["DECLARATION_SCHEMA"], "operation": operation, "input_path": data,
                       "reference_path": name, "column": "measurement", "absolute_tolerance": tolerance}
        if operation == OPS["GROUPED"]:
            declaration["group_column"] = "arm"
        declaration.update(extra)
        return declaration

    def pin(self, operation, values=None, **kwargs):
        return OPS["prepare"](self.root, self.declare(operation, EXPECTED[operation] if values is None else values, **kwargs))

    def forged(self, pin, **changes):
        forged = {key: value for key, value in pin.items() if key != "pin_sha256"}
        forged.update(changes)
        forged["pin_sha256"] = ENGINE["_digest"](forged)
        return forged

    def assert_honest(self, receipt, status, reason, execution):
        self.assertEqual((receipt["schema"], receipt["status"], receipt["reason"], receipt["execution"]),
                         (OPS["RECEIPT_SCHEMA"], status, reason, execution))
        self.assertEqual((receipt["evidence_scope"], receipt["signed"], receipt["independent_witness"],
                          receipt["scientific_claims_verified"]), ("SAME_HOST_LOCAL", False, False, False))
        self.assertEqual(receipt["receipt_sha256"],
                         ENGINE["_digest"]({k: v for k, v in receipt.items() if k != "receipt_sha256"}))

    def test_each_operation_matches_its_exact_reference(self):
        for operation, values in EXPECTED.items():
            with self.subTest(operation=operation):
                pin = self.pin(operation)
                self.assertEqual(pin["engine_sha256"], MAIN_ENGINE_SHA256)
                self.assertEqual(pin["engine_ops_sha256"],
                                 hashlib.sha256((SKILL / "scripts" / "engine_ops.py").read_bytes()).hexdigest())
                receipt = OPS["replay"](self.root, pin)
                self.assert_honest(receipt, "MATCH", "WITHIN_TOLERANCE", "COMPLETED")
                self.assertEqual(receipt["pin_sha256"], pin["pin_sha256"])
                self.assertEqual((receipt["operation"], receipt["row_count"], receipt["arithmetic_paths"]),
                                 (operation, 5, ["DECIMAL", "FRACTION"]))
                self.assertEqual({item["label"]: item["observed"] for item in receipt["results"]}, values)
                self.assertTrue(all(item["absolute_error"] == "0" and item["within"] for item in receipt["results"]))
                self.assertEqual(receipt, OPS["replay"](self.root, pin))

    def test_each_operation_diverges_outside_tolerance_and_matches_at_the_boundary(self):
        for operation, values in EXPECTED.items():
            shifted = {label: str(Decimal(value) + Decimal("0.001")) for label, value in values.items()}
            with self.subTest(operation=operation):
                receipt = OPS["replay"](self.root, self.pin(operation, shifted))
                self.assert_honest(receipt, "DIVERGED", "OUTSIDE_TOLERANCE", "COMPLETED")
                self.assertEqual({item["absolute_error"] for item in receipt["results"]}, {"0.001"})
                self.assertFalse(any(item["within"] for item in receipt["results"]))
                bounded = OPS["replay"](self.root, self.pin(operation, shifted, tolerance="0.001", name="bounded.json"))
                self.assertEqual((bounded["status"], bounded["absolute_tolerance"]), ("MATCH", "0.001"))

    def test_one_divergent_group_diverges_the_receipt(self):
        receipt = OPS["replay"](self.root, self.pin("csv_grouped_mean_v1", {"x": "2.5", "y": "2.25"}))
        self.assertEqual(receipt["status"], "DIVERGED")
        self.assertEqual([(item["label"], item["row_count"], item["within"]) for item in receipt["results"]],
                         [("x", 2, True), ("y", 3, False)])

    def test_even_median_and_non_terminating_mean_are_exact(self):
        (self.root / "even.csv").write_text("arm,measurement\nx,1\nx,2\ny,4\ny,10\n", encoding="utf-8")
        median = OPS["replay"](self.root, self.pin("csv_column_median_v1", {"measurement": "3"}, data="even.csv"))
        self.assertEqual((median["status"], median["results"][0]["observed"]), ("MATCH", "3"))
        (self.root / "thirds.csv").write_text("arm,measurement\nx,0\nx,0\nx,1\n", encoding="utf-8")
        mean = OPS["replay"](self.root, self.pin("csv_grouped_mean_v1", {"x": "0.3333"}, tolerance="0.0001",
                                                  data="thirds.csv", name="thirds.json"))
        self.assertEqual((mean["status"], mean["results"][0]["observed"], mean["results"][0]["absolute_error"]),
                         ("MATCH", "1/3", "1/30000"))

    def test_paths_use_independent_variance_formulas_that_agree(self):
        texts = ["1", "2", "4"]
        self.assertEqual(OPS["_decimal_path"]("csv_column_variance_v1", texts)[0], 14)  # 3 * 21 - 7 ** 2
        self.assertEqual(OPS["_decimal_path"]("csv_column_variance_v1", texts)[1], 6)  # n * (n - 1)
        self.assertEqual(OPS["_fraction_path"]("csv_column_variance_v1", texts), Fraction(7, 3))

    def test_tampered_or_missing_evidence_is_incomplete_and_not_executed(self):
        pin = self.pin("csv_column_sum_v1")
        (self.root / "data.csv").write_text(DATA.replace("0.5", "0.6"), encoding="utf-8", newline="")
        receipt = OPS["replay"](self.root, pin)
        self.assert_honest(receipt, "INCOMPLETE", "INPUT_CHANGED", "NOT_RUN")
        self.assertNotIn("results", receipt)
        (self.root / "data.csv").write_text(DATA, encoding="utf-8", newline="")
        self.assertEqual(OPS["replay"](self.root, pin)["status"], "MATCH")
        (self.root / "ref.json").write_text(json.dumps({"schema": OPS["REFERENCE_SCHEMA"], "operation": "csv_column_sum_v1",
                                                         "values": {"measurement": "12"}}), encoding="utf-8")
        self.assertEqual(OPS["replay"](self.root, pin)["reason"], "REFERENCE_CHANGED")
        (self.root / "data.csv").unlink()
        self.assertEqual(OPS["replay"](self.root, pin)["reason"], "INPUT_MISSING")

    def test_internal_disagreement_between_paths_is_incomplete(self):
        pin = self.pin("csv_column_variance_v1")
        for name, wrong in (("_fraction_path", lambda operation, texts: Fraction(1825, 1000) + Fraction(1, 10 ** 30)),
                            ("_decimal_path", lambda operation, texts: (1826, 1000))):
            with self.subTest(path=name), mock.patch.dict(OPS["replay"].__globals__, {name: wrong}):
                receipt = OPS["replay"](self.root, pin)
                self.assert_honest(receipt, "INCOMPLETE", "INTERNAL_DISAGREEMENT", "COMPLETED")
                self.assertNotIn("results", receipt)
                with self.assertRaisesRegex(ValueError, "INTERNAL_DISAGREEMENT"):
                    OPS["prepare"](self.root, self.declare("csv_column_variance_v1", EXPECTED["csv_column_variance_v1"]))

    def test_unsupported_operation_is_refused(self):
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_OPERATION"):
            OPS["prepare"](self.root, json.loads((self.root / "assets" / "ops-unsupported.json").read_text(encoding="utf-8")))
        code, _ = quiet(["prepare", "assets/ops-unsupported.json", "--root", str(self.root), "--output", "bad-pin.json"])
        self.assertEqual(code, 2)
        self.assertFalse((self.root / "bad-pin.json").exists())
        pin = self.pin("csv_column_sum_v1")
        for operation in ("csv_column_mean_v1", "python_eval_v1", ["csv_column_sum_v1"]):
            with self.subTest(operation=operation):
                receipt = OPS["replay"](self.root, self.forged(pin, operation=operation))
                self.assert_honest(receipt, "REFUSED", "UNSUPPORTED_OPERATION", "NOT_RUN")
                self.assertIsNone(receipt["pin_sha256"])

    def test_invalid_pins_are_refused(self):
        pin = self.pin("csv_grouped_mean_v1")
        cases = {"tampered": dict(pin, column="arm"),
                 "group equals column": self.forged(pin, group_column="measurement"),
                 "group on column op": self.forged(pin, operation="csv_column_sum_v1"),
                 "extra field": self.forged(pin, note="x"),
                 "bad ops digest": self.forged(pin, engine_ops_sha256="F" * 64),
                 "unsafe path": self.forged(pin, input=dict(pin["input"], path="../data.csv")),
                 "negative tolerance": self.forged(pin, absolute_tolerance="-1"),
                 "v1 schema": dict(pin, schema=ENGINE["SCHEMA"])}
        for name, bad in cases.items():
            with self.subTest(case=name):
                receipt = OPS["replay"](self.root, bad)
                self.assert_honest(receipt, "REFUSED", "INVALID_PIN", "NOT_RUN")
                self.assertIsNone(receipt["pin_sha256"])

    def test_engine_or_engine_ops_drift_is_incomplete(self):
        pin = self.pin("csv_column_count_v1")
        for key, reason in (("engine_ops_sha256", "ENGINE_OPS_CHANGED"), ("engine_sha256", "ENGINE_CHANGED")):
            with self.subTest(key=key):
                receipt = OPS["replay"](self.root, self.forged(pin, **{key: "0" * 64}))
                self.assert_honest(receipt, "INCOMPLETE", reason, "NOT_RUN")
                self.assertEqual(receipt[key + "_observed"], pin[key])
        with mock.patch.dict(OPS["replay"].__globals__, {"_ops_hash": mock.Mock(side_effect=OSError("gone"))}):
            self.assertEqual(OPS["replay"](self.root, pin)["reason"], "ENGINE_OPS_UNREADABLE")

    def test_declaration_and_reference_strictness(self):
        (self.root / "one.csv").write_text("measurement\n2.5\n", encoding="utf-8")
        total, grouped = EXPECTED["csv_column_sum_v1"], EXPECTED["csv_grouped_mean_v1"]
        cases = {
            "extra field": ("csv_column_sum_v1", total, {"note": "x"}, "INVALID_PIN"),
            "reserved field": ("csv_column_sum_v1", total, {"engine_sha256": "0" * 64}, "unsupported declaration"),
            "group on column op": ("csv_column_sum_v1", total, {"group_column": "arm"}, "INVALID_PIN"),
            "group equals column": ("csv_grouped_mean_v1", grouped, {"group_column": "measurement"}, "INVALID_PIN"),
            "missing group": ("csv_grouped_mean_v1", {"x": "2.5"}, {}, "PINNED_CONTENT_INVALID"),
            "extra group": ("csv_grouped_mean_v1", dict(grouped, z="1"), {}, "PINNED_CONTENT_INVALID"),
            "absent group column": ("csv_grouped_mean_v1", grouped, {"group_column": "site"}, "PINNED_CONTENT_INVALID"),
            "number not string": ("csv_column_sum_v1", {"measurement": 11}, {}, "PINNED_CONTENT_INVALID"),
            "label not column": ("csv_column_sum_v1", {"value": "11"}, {}, "PINNED_CONTENT_INVALID"),
            "v1 declaration schema": ("csv_column_sum_v1", total, {"schema": ENGINE["DECLARATION_SCHEMA"]}, "unsupported declaration"),
            "one-row variance": ("csv_column_variance_v1", {"measurement": "0"}, {"input_path": "one.csv"}, "PINNED_CONTENT_INVALID"),
            "v1 reference": ("csv_column_sum_v1", total, {"reference_path": "assets/expected.json"}, "PINNED_CONTENT_INVALID"),
        }
        for index, (name, (operation, values, extra, reason)) in enumerate(cases.items()):
            with self.subTest(case=name), self.assertRaisesRegex(ValueError, reason):
                OPS["prepare"](self.root, self.declare(operation, values, name="case-%d.json" % index, **extra))
        declaration = self.declare("csv_column_sum_v1", total, name="other-op.json")
        (self.root / "other-op.json").write_text(json.dumps({"schema": OPS["REFERENCE_SCHEMA"], "operation": "csv_column_count_v1",
                                                             "values": total}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "PINNED_CONTENT_INVALID"):
            OPS["prepare"](self.root, declaration)
        self.assertEqual(OPS["replay"](self.root, self.pin("csv_column_sum_v1", name="ok.json"))["status"], "MATCH")

    def test_cli_rejects_duplicate_keys_and_non_finite_json_with_exit_2(self):
        good = json.dumps(self.declare("csv_column_sum_v1", EXPECTED["csv_column_sum_v1"]))
        documents = {"dup.json": good[:-1] + ', "column": "measurement"}',
                     "nan.json": good.replace('"absolute_tolerance": "0"', '"absolute_tolerance": NaN'),
                     "inf.json": good.replace('"absolute_tolerance": "0"', '"absolute_tolerance": Infinity')}
        for name, text in documents.items():
            (self.root / name).write_text(text, encoding="utf-8")
            with self.subTest(document=name):
                self.assertEqual(quiet(["prepare", name, "--root", str(self.root), "--output", name + ".pin"])[0], 2)
                self.assertFalse((self.root / (name + ".pin")).exists())
        (self.root / "ref.json").write_text('{"schema": "%s", "operation": "csv_column_sum_v1", "values": {"measurement": "11"},'
                                            ' "values": {"measurement": "11"}}' % OPS["REFERENCE_SCHEMA"], encoding="utf-8")
        (self.root / "decl.json").write_text(good, encoding="utf-8")
        self.assertEqual(quiet(["prepare", "decl.json", "--root", str(self.root), "--output", "p.json"])[0], 2)
        pin_text = json.dumps(self.pin("csv_column_sum_v1", name="ok.json"))
        (self.root / "dup-pin.json").write_text(pin_text[:-1] + ', "column": "measurement"}', encoding="utf-8")
        code, _ = quiet(["replay", "dup-pin.json", "--root", str(self.root), "--receipt", "dup-receipt.json"])
        receipt = json.loads((self.root / "dup-receipt.json").read_text(encoding="utf-8"))
        self.assertEqual((code, receipt["status"], receipt["reason"]), (2, "REFUSED", "INVALID_PIN"))

    def test_shipped_v2_fixtures_through_the_isolated_cli(self):
        script = str(SKILL / "scripts" / "run.py")
        run = lambda *args: subprocess.run([sys.executable, "-I", "-B", script, *args, "--root", str(self.root)],
                                           capture_output=True, text=True, timeout=30)
        prepared = run("prepare", "assets/ops-declaration.json", "--output", "ops-pin.json")
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        replayed = run("replay", "ops-pin.json", "--receipt", "ops-receipt.json")
        self.assertEqual(replayed.returncode, 0, replayed.stderr)
        receipt = json.loads((self.root / "ops-receipt.json").read_text(encoding="utf-8"))
        self.assertEqual((receipt["schema"], receipt["status"], receipt["results"][0]["observed"]),
                         (OPS["RECEIPT_SCHEMA"], "MATCH", "0.5"))
        self.assertEqual(json.loads(replayed.stdout)["receipt_sha256"], receipt["receipt_sha256"])
        refused = run("prepare", "assets/ops-unsupported.json", "--output", "bad-pin.json")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("UNSUPPORTED_OPERATION", refused.stderr)
        self.assertFalse((self.root / "bad-pin.json").exists())
        pin = json.loads((self.root / "ops-pin.json").read_text(encoding="utf-8"))
        (self.root / "assets" / "ops-expected.json").write_text(json.dumps(
            {"schema": OPS["REFERENCE_SCHEMA"], "operation": "csv_column_variance_v1", "values": {"measurement": "0.6"}}), encoding="utf-8")
        self.assertEqual(OPS["replay"](self.root, pin)["reason"], "REFERENCE_CHANGED")
        self.assertEqual(run("replay", "ops-pin.json", "--receipt", "changed.json").returncode, 2)

    def test_engine_py_bytes_are_unchanged_from_main(self):
        self.assertEqual(hashlib.sha256((SKILL / "scripts" / "engine.py").read_bytes()).hexdigest(), MAIN_ENGINE_SHA256)
        self.assertEqual(ENGINE["_engine_hash"](), MAIN_ENGINE_SHA256)

    def test_v1_example_pin_and_receipt_are_byte_identical_to_main(self):
        code, out = quiet(["prepare", "assets/declaration.json", "--root", str(self.root), "--output", "pin.json"])
        self.assertEqual((code, json.loads(out)), (0, {"output": "pin.json", "pin_sha256": MAIN_V1_PIN_SHA256, "status": "PINNED"}))
        code, out = quiet(["replay", "pin.json", "--root", str(self.root), "--receipt", "receipt.json"])
        self.assertEqual((code, json.loads(out)), (0, {"reason": "WITHIN_TOLERANCE", "receipt": "receipt.json",
                                                       "receipt_sha256": MAIN_V1_RECEIPT_SHA256, "status": "MATCH"}))
        self.assertEqual(hashlib.sha256((self.root / "pin.json").read_bytes()).hexdigest(), MAIN_V1_PIN_FILE_SHA256)
        self.assertEqual(hashlib.sha256((self.root / "receipt.json").read_bytes()).hexdigest(), MAIN_V1_RECEIPT_FILE_SHA256)
        declaration = json.loads((self.root / "assets" / "declaration.json").read_text(encoding="utf-8"))
        self.assertEqual(ENGINE["replay"](self.root, ENGINE["prepare"](self.root, declaration))["receipt_sha256"],
                         MAIN_V1_RECEIPT_SHA256)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            OPS["prepare"](self.root, declaration)

    def test_installer_bundle_carries_parseable_engine_ops(self):
        installer = runpy.run_path(str(ROOT / "tools" / "install_claude_science.py"))
        files = installer["bundle"](ROOT, family="replay")["szl-experiment-replay"]
        self.assertTrue({"scripts/engine_ops.py", "references/contract.md", "references/provenance.md"} <= set(files))
        ast.parse(files["scripts/engine_ops.py"], filename="scripts/engine_ops.py")


if __name__ == "__main__":
    unittest.main()
