"""Numerical and fail-closed regressions for the synthetic uncertainty model."""

from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "skills" / "szl-uncertainty-lineage"
SCRIPT = ROOT / "scripts" / "run.py"
SPEC = importlib.util.spec_from_file_location("uncertainty_lineage", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def model(mode: str = "declared_independent") -> dict:
    correlation = {"mode": mode}
    if mode == "correlation_matrix":
        correlation = {
            "mode": mode,
            "order": ["length", "width"],
            "matrix": [[1.0, 0.25], [0.25, 1.0]],
        }
    return {
        "schema": MODULE.SCHEMA,
        "inputs": {
            "length": {"value": 2.0, "sigma": 0.1},
            "width": {"value": 3.0, "sigma": 0.2},
        },
        "correlation": correlation,
        "nodes": [{"id": "area", "op": "multiply", "args": ["length", "width"]}],
        "targets": ["area"],
    }


def evaluate(payload: dict) -> dict:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return MODULE.evaluate(payload, raw)


class UncertaintyLineageTests(unittest.TestCase):
    def test_independent_product_matches_analytic_reference(self) -> None:
        report = evaluate(model())["targets"][0]
        self.assertEqual(report["value"], 6.0)
        self.assertAlmostEqual(report["variance"], 0.25, places=12)
        self.assertAlmostEqual(report["standard_uncertainty"], 0.5, places=12)
        self.assertEqual(report["jacobian"], {"length": 3.0, "width": 2.0})

    def test_shared_correlation_changes_variance_by_declared_cross_term(self) -> None:
        report = evaluate(model("correlation_matrix"))["targets"][0]
        self.assertAlmostEqual(report["variance"], 0.31, places=12)
        cross = next(
            row["variance_contribution"]
            for row in report["covariance_contributions"]
            if row["inputs"] == ["length", "width"]
        )
        self.assertAlmostEqual(cross, 0.06, places=12)

    def test_perfect_correlation_is_valid_semidefinite(self) -> None:
        payload = model("correlation_matrix")
        payload["correlation"]["matrix"] = [[1.0, 1.0], [1.0, 1.0]]
        self.assertAlmostEqual(evaluate(payload)["targets"][0]["variance"], 0.49, places=12)

    def test_correlated_correction_matches_independent_expansion(self) -> None:
        payload = {
            "schema": MODULE.SCHEMA,
            "inputs": {
                "raw": {"value": 10, "sigma": 0.2},
                "offset": {"value": 1, "sigma": 0.1},
                "gain": {"value": 2, "sigma": 0.05},
            },
            "correlation": {"mode": "correlation_matrix", "order": ["raw", "offset", "gain"],
                            "matrix": [[1, 0.5, 0], [0.5, 1, 0], [0, 0, 1]]},
            "nodes": [
                {"id": "corrected", "op": "subtract", "args": ["raw", "offset"]},
                {"id": "result", "op": "multiply", "args": ["corrected", "gain"]},
            ],
            "targets": ["result"],
        }
        result = evaluate(payload)["targets"][0]
        expected = 4 * 0.2**2 + 4 * 0.1**2 + 81 * 0.05**2 - 8 * 0.2 * 0.1 * 0.5
        self.assertEqual(result["value"], 18)
        self.assertEqual(result["jacobian"], {"raw": 2, "offset": -2, "gain": 9})
        self.assertAlmostEqual(result["variance"], expected, places=12)
        self.assertAlmostEqual(result["standard_uncertainty"], math.sqrt(expected), places=12)

    def test_pairwise_plausible_but_non_psd_correlation_fails(self) -> None:
        payload = model("correlation_matrix")
        payload["inputs"]["height"] = {"value": 4.0, "sigma": 0.3}
        payload["correlation"]["order"] = ["length", "width", "height"]
        payload["correlation"]["matrix"] = [
            [1.0, -0.9, -0.9],
            [-0.9, 1.0, -0.9],
            [-0.9, -0.9, 1.0],
        ]
        with self.assertRaisesRegex(MODULE.ContractError, "positive semidefinite"):
            evaluate(payload)

    def test_near_indefinite_matrix_is_not_rounded_to_psd(self) -> None:
        payload = model("correlation_matrix")
        payload["inputs"]["height"] = {"value": 4.0, "sigma": 0.3}
        payload["correlation"]["order"] = ["length", "width", "height"]
        payload["correlation"]["matrix"] = [
            [1, -0.50000000001, -0.50000000001],
            [-0.50000000001, 1, -0.50000000001],
            [-0.50000000001, -0.50000000001, 1],
        ]
        with self.assertRaisesRegex(MODULE.ContractError, "positive semidefinite"):
            evaluate(payload)

    def test_eighty_digit_near_indefinite_matrix_fails_exact_check(self) -> None:
        payload = model("correlation_matrix")
        payload["inputs"]["height"] = {"value": 4.0, "sigma": 0.3}
        payload["correlation"]["order"] = ["length", "width", "height"]
        edge = Decimal("-0.5" + "0" * 78 + "1")
        payload["correlation"]["matrix"] = [
            [1, edge, edge], [edge, 1, edge], [edge, edge, 1]
        ]
        with self.assertRaisesRegex(MODULE.ContractError, "positive semidefinite"):
            MODULE.evaluate(payload, b"synthetic 80-digit matrix")

    def test_cli_valid_near_perfect_correlation_preserves_nonzero_variance(self) -> None:
        edge = "0." + "9" * 80
        raw = ('{"schema":"' + MODULE.SCHEMA + '","inputs":'
               '{"a":{"value":1,"sigma":1},"b":{"value":1,"sigma":1}},'
               '"correlation":{"mode":"correlation_matrix","order":["a","b"],'
               '"matrix":[[1,' + edge + '],[' + edge + ',1]]},'
               '"nodes":[{"id":"difference","op":"subtract","args":["a","b"]}],'
               '"targets":["difference"]}').encode()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "near-perfect.json"
            path.write_bytes(raw)
            result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), str(path)],
                                    capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        target = json.loads(result.stdout)["targets"][0]
        self.assertEqual(target["value"], 0)
        self.assertEqual(target["variance"], 2e-80)
        self.assertTrue(math.isclose(target["standard_uncertainty"], math.sqrt(2) * 1e-40,
                                     rel_tol=1e-15, abs_tol=0))

    def test_quotient_scales_preserve_nonzero_uncertainty(self) -> None:
        for scale, sigma in ((1e155, 1e154), (1e-155, 1e-156), (1e-200, 1e-201)):
            with self.subTest(scale=scale):
                payload = model()
                payload["inputs"]["length"] = {"value": scale, "sigma": sigma}
                payload["inputs"]["width"] = {"value": scale, "sigma": 0}
                payload["nodes"] = [{"id": "ratio", "op": "divide", "args": ["length", "width"]}]
                payload["targets"] = ["ratio"]
                result = evaluate(payload)["targets"][0]
                self.assertEqual(result["value"], 1.0)
                self.assertAlmostEqual(result["standard_uncertainty"], 0.1, places=12)
                self.assertAlmostEqual(result["variance"], 0.01, places=12)

    def test_cli_inexact_cancellation_and_nonterminating_quotient_fail_closed(self) -> None:
        payload = model()
        payload["inputs"] = {"a": {"value": 1e100, "sigma": 0}, "b": {"value": 1, "sigma": 1}}
        payload["nodes"] = [{"id": "sum", "op": "add", "args": ["a", "b"]},
                            {"id": "result", "op": "subtract", "args": ["sum", "a"]}]
        payload["targets"] = ["result"]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.json"
            for document in (payload, model()):
                if document is not payload:
                    document["inputs"]["length"]["value"] = 1
                    document["nodes"] = [{"id": "area", "op": "divide", "args": ["length", "width"]}]
                path.write_text(json.dumps(document), encoding="utf-8")
                rejected = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), str(path)],
                                          capture_output=True, text=True, check=False)
                self.assertEqual(rejected.returncode, 2)
                self.assertEqual(rejected.stdout, "")
                self.assertIn("inexact at 80-digit precision", rejected.stderr)
                self.assertNotIn("Traceback", rejected.stderr)

    def test_intermediate_exponent_is_bounded_before_fraction_conversion(self) -> None:
        payload = model()
        payload["inputs"] = {"a": {"value": Decimal("1e400"), "sigma": 0}}
        payload["nodes"] = [{"id": "square", "op": "multiply", "args": ["a", "a"]},
                            {"id": "fourth", "op": "multiply", "args": ["square", "square"]}]
        payload["targets"] = ["fourth"]
        with self.assertRaisesRegex(MODULE.ContractError, "intermediate exponent bound"):
            MODULE.evaluate(payload, b"synthetic bounded exponent")

    def test_cli_extreme_exponent_is_a_typed_parse_failure(self) -> None:
        raw = json.dumps(model()).replace("2.0", "1e999999999999999999999999999999", 1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "huge-exponent.json"
            path.write_text(raw, encoding="utf-8")
            rejected = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), str(path)],
                                      capture_output=True, text=True, check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(rejected.stdout, "")
        self.assertIn("uncertainty-lineage:", rejected.stderr)
        self.assertNotIn("Traceback", rejected.stderr)

    def test_missing_correlation_is_not_silently_independent(self) -> None:
        payload = model()
        del payload["correlation"]
        with self.assertRaises(MODULE.ContractError):
            evaluate(payload)

    def test_zero_local_gradient_does_not_erase_declared_ancestry(self) -> None:
        payload = model()
        payload["nodes"] = [
            {"id": "cancel", "op": "subtract", "args": ["length", "length"]}
        ]
        payload["targets"] = ["cancel"]
        report = evaluate(payload)["targets"][0]
        self.assertEqual(report["variance"], 0.0)
        self.assertEqual(report["declared_inputs"], ["length"])

    def test_divide_by_zero_and_forward_reference_fail(self) -> None:
        payload = model()
        payload["inputs"]["width"]["value"] = 0.0
        payload["nodes"] = [{"id": "ratio", "op": "divide", "args": ["length", "width"]}]
        payload["targets"] = ["ratio"]
        with self.assertRaisesRegex(MODULE.ContractError, "divides by zero"):
            evaluate(payload)
        payload["nodes"] = [{"id": "ratio", "op": "add", "args": ["length", "later"]}]
        with self.assertRaisesRegex(MODULE.ContractError, "undeclared or later"):
            evaluate(payload)

    def test_boolean_input_and_nonfinite_intermediate_fail(self) -> None:
        payload = model()
        payload["inputs"]["length"]["value"] = True
        with self.assertRaisesRegex(MODULE.ContractError, "finite number"):
            evaluate(payload)
        payload["inputs"]["length"]["value"] = 1e200
        payload["inputs"]["width"]["value"] = 1e200
        with self.assertRaisesRegex(MODULE.ContractError, "outside finite JSON number range"):
            evaluate(payload)

    def test_cli_binds_raw_bytes_and_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.json"
            path.write_bytes((ROOT / "assets" / "example.json").read_bytes())
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(path)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["input_sha256"], __import__("hashlib").sha256(path.read_bytes()).hexdigest())
            self.assertTrue(math.isfinite(report["targets"][0]["standard_uncertainty"]))
            path.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
            rejected = subprocess.run(
                [sys.executable, str(SCRIPT), str(path)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("duplicate JSON key", rejected.stderr)
            extreme = model()
            extreme["inputs"]["length"] = {"value": 1e-200, "sigma": 1e-201}
            extreme["inputs"]["width"] = {"value": 1e-200, "sigma": 0}
            extreme["nodes"] = [{"id": "ratio", "op": "divide", "args": ["length", "width"]}]
            extreme["targets"] = ["ratio"]
            path.write_text(json.dumps(extreme), encoding="utf-8")
            accepted = subprocess.run(
                [sys.executable, str(SCRIPT), str(path)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertAlmostEqual(json.loads(accepted.stdout)["targets"][0]["standard_uncertainty"], 0.1)
            path.write_text(path.read_text().replace("1e-200", "1e-400"), encoding="utf-8")
            underflow = subprocess.run(
                [sys.executable, str(SCRIPT), str(path)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(underflow.returncode, 2)
            self.assertNotIn("Traceback", underflow.stderr)


if __name__ == "__main__":
    unittest.main()
