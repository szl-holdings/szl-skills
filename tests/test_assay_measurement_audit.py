"""Fail-closed checks for the declared, offline assay measurement audit."""

import copy
import contextlib
import decimal
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-assay-measurement-audit"
SPEC = importlib.util.spec_from_file_location("szl_assay_measurement_audit", SKILL / "kernel.py")
KERNEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(KERNEL)
RUN_SPEC = importlib.util.spec_from_file_location("szl_assay_measurement_run", SKILL / "scripts" / "run.py")
RUNNER = importlib.util.module_from_spec(RUN_SPEC)
RUN_SPEC.loader.exec_module(RUNNER)


def fixture(name="example.json"):
    return json.loads((SKILL / "assets" / name).read_text(encoding="utf-8"))


class AssayMeasurementAuditTests(unittest.TestCase):
    def test_example_releases_only_declared_checks(self):
        report = KERNEL.szl_audit_assay_measurement(fixture())
        self.assertEqual(report["status"], "PASS_DECLARED_CHECKS")
        self.assertEqual([decimal.Decimal(sample["concentration"]) for sample in report["samples"]],
                         [decimal.Decimal("3"), decimal.Decimal("2")])
        self.assertEqual(report["samples"][0]["expanded_uncertainty"], "0.10")
        self.assertEqual(report["samples"][0]["uncertainty_basis"], "FINAL_REPORTED_CONCENTRATION")
        self.assertTrue(report["samples"][0]["dilution_uncertainty_included"])
        self.assertEqual(report["boundaries"]["clinical_use"], "NOT_QUALIFIED")

    def test_bad_quality_control_withholds_every_concentration(self):
        report = KERNEL.szl_audit_assay_measurement(fixture("control-failure.json"))
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("QC_OUT_OF_LIMIT", {finding["code"] for finding in report["controls"]["findings"]})
        self.assertTrue(all(sample["status"] == "HOLD" and sample["concentration"] is None
                            for sample in report["samples"]))

    def test_uncertainty_must_bind_to_final_dilution_corrected_result(self):
        for change, expected in (
            ({"uncertainty_basis": None}, "UNCERTAINTY_NOT_DECLARED"),
            ({"uncertainty_basis": "PRE_DILUTION"}, "UNCERTAINTY_BASIS_NOT_FINAL"),
            ({"dilution_uncertainty_included": False}, "DILUTION_UNCERTAINTY_NOT_INCLUDED"),
        ):
            with self.subTest(change=change):
                record = fixture()
                sample = record["samples"][0]
                for key, value in change.items():
                    if value is None:
                        sample.pop(key)
                    else:
                        sample[key] = value
                report = KERNEL.szl_audit_assay_measurement(record)
                first = report["samples"][0]
                self.assertEqual((report["status"], first["status"], first["concentration"]),
                                 ("HOLD", "HOLD", None))
                self.assertIn(expected, {finding["code"] for finding in first["findings"]})
                self.assertEqual(report["samples"][1]["status"], "PASS_DECLARED_CHECKS")

    def test_invalid_numbers_and_duplicate_ids_fail_closed(self):
        for mutate in (
            lambda x: x["samples"][0].update(dilution_factor="0.5"),
            lambda x: x["samples"][0].update(coverage_factor="0.9"),
            lambda x: x["samples"][0].update(dilution_uncertainty_included="true"),
            lambda x: x["samples"][0].update(signal="NaN"),
            lambda x: x["samples"][1].update(sample_id=x["samples"][0]["sample_id"]),
            lambda x: x["samples"][1].update(well_id=x["blank"]["well_id"]),
        ):
            with self.subTest(mutate=mutate):
                record = fixture()
                mutate(record)
                with self.assertRaises(ValueError):
                    KERNEL.szl_audit_assay_measurement(record)

    def test_out_of_range_or_below_quantification_is_held(self):
        record = fixture()
        record["samples"][0]["signal"] = "9"
        record["samples"][1]["signal"] = "0.07"  # In calibration range, below declared 0.20.
        report = KERNEL.szl_audit_assay_measurement(record)
        self.assertEqual(report["status"], "HOLD")
        self.assertEqual([sample["concentration"] for sample in report["samples"]], [None, None])
        self.assertIn("OUTSIDE_CALIBRATION_RANGE", {f["code"] for f in report["samples"][0]["findings"]})
        self.assertIn("BELOW_DECLARED_QUANTIFICATION_MIN", {f["code"] for f in report["samples"][1]["findings"]})

    def test_extreme_but_allowed_calibrators_preserve_exact_line(self):
        # A 50-digit Decimal context loses these 1e-30 offsets near 1e30.
        record = fixture()
        x0 = "999999999999999999999999999999.999999999999999999999999999998"
        x1 = "999999999999999999999999999999.999999999999999999999999999999"
        x2 = "1000000000000000000000000000000"
        record["calibrators"] = [
            {"well_id": "A1", "concentration": x0, "unit": "mg/L", "signal": "0"},
            {"well_id": "A2", "concentration": x1, "unit": "mg/L", "signal": "1"},
            {"well_id": "A3", "concentration": x2, "unit": "mg/L", "signal": "2"},
        ]
        record["limits"].update(max_abs_standard_residual="0", max_abs_blank_signal="0",
                                max_qc_relative_error="0", quantification_min=x0)
        record["blank"]["signal"] = "0"
        record["quality_controls"][0].update(target_concentration=x0, signal="0")
        record["quality_controls"][1].update(target_concentration=x2, signal="2")
        record["samples"] = [copy.deepcopy(record["samples"][1])]
        record["samples"][0].update(signal="1", dilution_factor="1")
        report = KERNEL.szl_audit_assay_measurement(record)
        self.assertEqual(report["status"], "PASS_DECLARED_CHECKS")
        self.assertEqual([decimal.Decimal(value) for value in report["calibration"]["range"]],
                         [decimal.Decimal(x0), decimal.Decimal(x2)])
        self.assertNotEqual(*report["calibration"]["range"])
        self.assertEqual(decimal.Decimal(report["calibration"]["slope"]), decimal.Decimal("1e30"))
        self.assertEqual(decimal.Decimal(report["calibration"]["max_abs_residual_observed"]), 0)

    def test_cli_rejects_duplicate_keys_and_preserves_existing_output(self):
        script = SKILL / "scripts" / "run.py"
        with tempfile.TemporaryDirectory() as directory:
            temp = pathlib.Path(directory)
            bad = temp / "duplicate.json"
            bad.write_text('{"schema":"a","schema":"b"}', encoding="utf-8")
            proc = subprocess.run([sys.executable, "-B", str(script), str(bad)],
                                  capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("Duplicate JSON key", proc.stderr)
            output = temp / "report.json"
            output.write_text("keep this", encoding="utf-8")
            proc = subprocess.run([sys.executable, "-B", str(script),
                                   str(SKILL / "assets" / "example.json"), "--output", str(output)],
                                  capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(output.read_text(encoding="utf-8"), "keep this")
            fresh = temp / "fresh-report.json"
            proc = subprocess.run([sys.executable, "-B", str(script),
                                   str(SKILL / "assets" / "example.json"), "--output", str(fresh)],
                                  capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(fresh.read_text(encoding="utf-8"))["status"],
                             "PASS_DECLARED_CHECKS")
            self.assertEqual(list(temp.glob(".szl-assay-*.tmp")), [])

    def test_write_failure_never_leaves_a_partial_final_report(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = pathlib.Path(directory)
            output = temp / "report.json"
            stderr = io.StringIO()
            with mock.patch.object(RUNNER.os, "fsync", side_effect=OSError("simulated disk full")):
                with contextlib.redirect_stderr(stderr):
                    code = RUNNER.main([str(SKILL / "assets" / "example.json"),
                                        "--output", str(output)])
            self.assertEqual(code, 2)
            self.assertIn("simulated disk full", stderr.getvalue())
            self.assertFalse(output.exists())
            self.assertEqual(list(temp.glob(".szl-assay-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
