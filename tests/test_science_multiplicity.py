"""Synthetic, offline regression checks for szl-multiplicity-audit."""
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from decimal import Inexact, localcontext


ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-multiplicity-audit"
SPEC = importlib.util.spec_from_file_location("szl_multiplicity_kernel", SKILL / "kernel.py")
KERNEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(KERNEL)


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def case(method="holm", p_values=("0.001", "0.04", "0.02", "0.07")):
    plan = {
        "schema": KERNEL.PLAN_SCHEMA, "family_id": "synthetic-primary", "alpha": "0.05",
        "method": method,
        "hypotheses": [{"id": key, "description": "Synthetic endpoint " + key}
                       for key in ("a", "b", "c", "d")],
    }
    if method == "bh":
        plan["dependence_assumption"] = "independent"
    plan_bytes = encoded(plan)
    results = {
        "schema": KERNEL.RESULTS_SCHEMA, "family_id": plan["family_id"],
        "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "results": [{"id": key, "p_value": p}
                    for key, p in zip(("a", "b", "c", "d"), p_values)],
        "extra_attempts": [],
    }
    return plan, results


def audit(plan, results):
    return KERNEL.szl_audit_multiplicity(encoded(plan), encoded(results))


class MultiplicityTests(unittest.TestCase):
    def test_fixture_is_byte_bound_and_holm_oracle(self):
        plan = (SKILL / "assets" / "plan.json").read_bytes()
        results = (SKILL / "assets" / "results.json").read_bytes()
        report = KERNEL.szl_audit_multiplicity(plan, results)
        self.assertEqual(report["status"], "COMPLETE")
        self.assertEqual(report["planned_count"], 4)
        self.assertEqual(report["rejections"], 1)
        self.assertEqual([row["adjusted_p_value"] for row in report["adjusted"]],
                         ["0.004", "0.08", "0.06", "0.08"])
        self.assertEqual([row["reject_at_alpha"] for row in report["adjusted"]],
                         [True, False, False, False])

    def test_bh_oracle_and_distinct_target(self):
        plan, results = case("bh")
        report = audit(plan, results)
        self.assertEqual(report["status"], "COMPLETE")
        self.assertEqual(report["rejections"], 2)
        self.assertEqual(report["adjusted"][0]["adjusted_p_value"], "0.004")
        self.assertTrue(report["adjusted"][1]["adjusted_p_value"].startswith("0.053333333333"))
        self.assertEqual(report["adjusted"][2]["adjusted_p_value"], "0.04")
        self.assertEqual(report["dependence_assumption_as_declared"], "independent")

    def test_ties_are_deterministic_and_equal(self):
        plan, results = case(p_values=("0.01", "0.01", "0.2", "0.4"))
        report = audit(plan, results)
        self.assertEqual(report["status"], "COMPLETE")
        self.assertEqual(report["adjusted"][0]["adjusted_p_value"], "0.04")
        self.assertEqual(report["adjusted"][1]["adjusted_p_value"], "0.04")
        plan["method"] = "bh"
        plan["dependence_assumption"] = "independent"
        results["plan_sha256"] = hashlib.sha256(encoded(plan)).hexdigest()
        b = audit(plan, results)
        self.assertEqual(b["adjusted"][0]["adjusted_p_value"], "0.02")
        self.assertEqual(b["adjusted"][1]["adjusted_p_value"], "0.02")

    def test_missing_null_extra_and_attempts_all_hold_without_arithmetic(self):
        for mutate, finding in (
            (lambda r: r["results"].pop(), "MISSING_PLANNED_RESULTS"),
            (lambda r: r["results"][0].update(p_value=None), "MISSING_P_VALUE"),
            (lambda r: r["results"].append({"id": "e", "p_value": "0.01"}), "UNPLANNED_RESULTS"),
            (lambda r: r["results"].append({"id": "a", "p_value": "0.02"}), "DUPLICATE_RESULT_ID"),
            (lambda r: r["extra_attempts"].append("second-look"), "EXTRA_ANALYSIS_ATTEMPTS"),
        ):
            with self.subTest(finding=finding):
                plan, results = case()
                mutate(results)
                report = audit(plan, results)
                self.assertEqual(report["status"], "HOLD")
                self.assertIn(finding, report["findings"])
                self.assertEqual(report["adjusted"], [])

    def test_plan_drift_and_family_mismatch_hold(self):
        plan, results = case()
        plan["hypotheses"][0]["description"] = "Changed after results"
        report = audit(plan, results)
        self.assertIn("PLAN_DIGEST_MISMATCH", report["findings"])
        self.assertEqual(report["adjusted"], [])
        plan, results = case()
        results["family_id"] = "other"
        self.assertIn("FAMILY_ID_MISMATCH", audit(plan, results)["findings"])

    def test_bh_unknown_dependence_holds(self):
        plan, results = case("bh")
        plan["dependence_assumption"] = "unknown"
        results["plan_sha256"] = hashlib.sha256(encoded(plan)).hexdigest()
        report = audit(plan, results)
        self.assertEqual(report["status"], "HOLD")
        self.assertIn("BH_DEPENDENCE_NOT_DECLARED", report["findings"])

    def test_invalid_numbers_and_family_shape_error(self):
        for invalid in ("-0.01", "1.01", "NaN", "Infinity", "", "1_0", True, "1e-1001"):
            with self.subTest(invalid=invalid):
                plan, results = case()
                results["results"][0]["p_value"] = invalid
                self.assertEqual(audit(plan, results)["status"], "ERROR")
        plan, results = case()
        plan["hypotheses"][1]["id"] = "a"
        self.assertEqual(audit(plan, results)["status"], "ERROR")
        plan, results = case()
        plan["alpha"] = "1"
        self.assertEqual(audit(plan, results)["status"], "ERROR")
        plan, results = case()
        results.pop("extra_attempts")
        self.assertEqual(audit(plan, results)["status"], "ERROR")

    def test_extreme_exponent_must_not_underflow_into_false_rejection(self):
        plan, results = case()
        plan["alpha"] = "1e-1000200"
        results["plan_sha256"] = hashlib.sha256(encoded(plan)).hexdigest()
        results["results"][0]["p_value"] = "6e-1000201"
        self.assertEqual(audit(plan, results)["status"], "ERROR")

    def test_ambient_decimal_context_cannot_change_a_decision(self):
        plan, results = case(p_values=("6e-201", "1", "1", "1"))
        plan["alpha"] = "1e-200"
        results["plan_sha256"] = hashlib.sha256(encoded(plan)).hexdigest()
        with localcontext() as context:
            context.Emin = -10
            context.traps[Inexact] = True
            report = audit(plan, results)
        self.assertEqual(report["status"], "COMPLETE")
        self.assertEqual(report["adjusted"][0]["adjusted_p_value"], "2.4E-200")
        self.assertFalse(report["adjusted"][0]["reject_at_alpha"])
        plan, results = case("bh")
        with localcontext() as context:
            context.Emin = -10
            context.traps[Inexact] = True
            self.assertEqual(audit(plan, results)["status"], "COMPLETE")

    def test_duplicate_json_keys_rejected(self):
        plan, results = case()
        raw = encoded(results).replace(b'"extra_attempts":[]',
                                       b'"extra_attempts":[],"extra_attempts":[]')
        self.assertEqual(KERNEL.szl_audit_multiplicity(encoded(plan), raw)["status"], "ERROR")

    def test_input_bounds_and_cli_exit_codes(self):
        plan, results = case()
        self.assertEqual(KERNEL.szl_audit_multiplicity(b"x" * (KERNEL.MAX_BYTES + 1), encoded(results))["status"], "ERROR")
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            p, r, report = root / "plan.json", root / "results.json", root / "report.json"
            p.write_bytes(encoded(plan))
            r.write_bytes(encoded(results))
            command = [sys.executable, str(SKILL / "scripts" / "run.py"), str(p), str(r), "--output", str(report)]
            complete = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(complete.returncode, 0, complete.stderr)
            self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["status"], "COMPLETE")
            second = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(second.returncode, 2)
            self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["status"], "COMPLETE")
            results["results"].pop()
            r.write_bytes(encoded(results))
            hold = subprocess.run(command[:-2], capture_output=True, text=True, timeout=20)
            self.assertEqual(hold.returncode, 1)
            self.assertEqual(json.loads(hold.stdout)["status"], "HOLD")


if __name__ == "__main__":
    unittest.main()
