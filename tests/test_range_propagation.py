"""Observable bounds, refusal and receipt checks for the standalone range skill."""

import ast
import builtins
from decimal import Decimal
from fractions import Fraction
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "pilots" / "range-propagation" / "szl-range-propagation"


def module_from(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


kernel = module_from(SKILL / "kernel.py", "range_kernel")


def example():
    return json.loads((SKILL / "assets" / "example.json").read_text(encoding="utf-8"))


class RangeTests(unittest.TestCase):
    def test_rectangle_rejects_underclaimed_bound_with_exact_rationals(self):
        result = kernel.audit(example())
        self.assertEqual(result["status"], "FAIL_DECLARED_BOUNDS")
        self.assertEqual((result["steps"][0]["lower_si"], result["steps"][0]["upper_si"]),
                         ("171/100", "231/100"))
        self.assertEqual(result["findings"],
                         [{"id": "area", "code": "CLAIM_DOES_NOT_CONTAIN_RANGE"}])
        self.assertFalse(result["probabilistic_coverage_established"])

    def test_exact_cm_conversion_and_wider_claim_pass(self):
        record = example()
        record["inputs"][0].update({"unit": "cm", "lower": "190", "upper": "210"})
        record["claims"][0].update({"lower_si": "1.7", "upper_si": "2.4"})
        result = kernel.audit(record)
        self.assertEqual(result["status"], "PASS_DECLARED_BOUNDS")
        self.assertEqual(result["steps"][0]["upper_si"], "231/100")

    def test_incompatible_dimensions_never_certify_claim(self):
        record = example()
        record["inputs"][1]["unit"] = "s"
        record["steps"][0]["operation"] = "sum"
        result = kernel.audit(record)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["findings"],
                         [{"id": "area", "code": "INCOMPATIBLE_DIMENSIONS"}])
        self.assertEqual(result["claims"], [])

    def test_zero_crossing_ratio_never_certifies_claim(self):
        record = example()
        record["inputs"][1].update({"lower": "-1", "upper": "1"})
        record["steps"][0]["operation"] = "ratio"
        record["claims"][0]["dimension"] = [0] * 7
        result = kernel.audit(record)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["findings"][0]["code"], "ZERO_CROSSING_DENOMINATOR")

    def test_repeated_variable_interval_is_conservative_not_tight(self):
        record = example()
        record["steps"][0] = {"id": "area", "operation": "difference",
                               "operands": ["length", "length"]}
        record["claims"][0].update({"dimension": [1, 0, 0, 0, 0, 0, 0],
                                      "lower_si": "0", "upper_si": "0"})
        result = kernel.audit(record)
        self.assertEqual(result["status"], "FAIL_DECLARED_BOUNDS")
        self.assertEqual((result["steps"][0]["lower_si"], result["steps"][0]["upper_si"]),
                         ("-1/5", "1/5"))

    def test_unknown_units_cycles_and_unbounded_numbers_refused(self):
        for change, code in (
            (lambda r: r["inputs"][0].update(unit="degC"), "UNKNOWN_UNIT"),
            (lambda r: r["steps"][0].update(operands=["area", "width"]),
             "UNKNOWN_OR_FUTURE_REFERENCE"),
            (lambda r: r["inputs"][0].update(lower="1e999"), "BAD_NUMBER"),
            (lambda r: r["inputs"][0].update(lower=float("nan")), "BAD_NUMBER"),
            (lambda r: r["claims"][0].update(dimension=[0] * 7), None),
        ):
            record = example()
            change(record)
            if code is None:
                self.assertEqual(kernel.audit(record)["findings"][0]["code"],
                                 "DIMENSION_MISMATCH")
            else:
                with self.assertRaises(ValueError) as caught:
                    kernel.audit(record)
                self.assertEqual(str(caught.exception), code)

    def test_unit_table_matches_released_source(self):
        original = module_from(ROOT / "skills" / "szl-unit-invariants" / "kernel.py",
                               "released_unit_kernel")
        self.assertEqual(set(kernel.UNITS), set(original.SZL_UNITS))
        for unit, (dimension, factor) in kernel.UNITS.items():
            expected_dim, expected_factor = original.SZL_UNITS[unit]
            self.assertEqual(tuple(expected_dim), dimension)
            self.assertEqual(Fraction(Decimal(expected_factor)), Fraction(Decimal(factor)))

    def test_rational_growth_limit_fails_closed(self):
        record = example()
        record["inputs"][0].update({"unit": "1", "lower": "999999999999999",
                                     "upper": "999999999999999"})
        record["inputs"][1].update({"unit": "1", "lower": "999999999999999",
                                     "upper": "999999999999999"})
        record["steps"] = [{"id": "power0", "operation": "product",
                            "operands": ["length", "width"]}]
        for index in range(1, 5):
            previous = f"power{index - 1}"
            record["steps"].append({"id": f"power{index}", "operation": "product",
                                    "operands": [previous, previous]})
        record["claims"] = [{"id": "power4", "dimension": [0] * 7,
                              "lower_si": "0", "upper_si": "999999999999999"}]
        with self.assertRaises(ValueError) as caught:
            kernel.audit(record)
        self.assertEqual(str(caught.exception), "COMPUTATION_LIMIT")

    def test_sidecar_entrypoint_uses_supported_module_shape(self):
        tree = ast.parse((SKILL / "kernel.py").read_text(encoding="utf-8"))
        for node in tree.body:
            self.assertIsInstance(node, (ast.Expr, ast.Import, ast.ImportFrom,
                                         ast.FunctionDef, ast.Assign, ast.AnnAssign))
            if isinstance(node, ast.FunctionDef):
                self.assertFalse(node.name.startswith("_"))
                self.assertNotIn(node.name, dir(builtins))
                self.assertEqual(node.decorator_list, [])
                for default in node.args.defaults:
                    ast.literal_eval(default)
        self.assertIs(kernel.audit, kernel.szl_range_audit)

    def test_cli_receipt_binds_exact_bytes_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "input.json"
            output = Path(directory) / "report.json"
            raw = (SKILL / "assets" / "example.json").read_bytes()
            original.write_bytes(raw)
            command = [sys.executable, "-B", str(SKILL / "scripts" / "run.py"),
                       str(original), "--output", str(output)]
            first = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(first.returncode, 1, first.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["input_bytes_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(report["status"], "FAIL_DECLARED_BOUNDS")
            snapshot = output.read_bytes()
            second = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(second.returncode, 2)
            self.assertEqual(output.read_bytes(), snapshot)

    def test_cli_duplicate_key_invalid_receipt_with_no_echo(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "input.json"
            original.write_text('{"schema":"secret_marker","schema":"other"}', encoding="utf-8")
            result = subprocess.run([sys.executable, "-B", str(SKILL / "scripts" / "run.py"),
                                     str(original)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            report = json.loads(result.stdout)
            self.assertEqual(report["error_code"], "DUPLICATE_KEY")
            self.assertNotIn("secret_marker", result.stdout + result.stderr)

    def test_cli_deep_json_returns_typed_invalid_input(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "input.json"
            original.write_text("[" * 1200 + "0" + "]" * 1200, encoding="utf-8")
            result = subprocess.run([sys.executable, "-B", str(SKILL / "scripts" / "run.py"),
                                     str(original)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "INVALID_INPUT")
            # Parser recursion limits vary by Python version. Either the JSON
            # decoder or the kernel must reject this non-object input safely.
            self.assertIn(report["error_code"], {"MALFORMED_JSON", "INVALID_FIELDS"})
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
