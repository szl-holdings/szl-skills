"""The prospective design helper must keep gaps and authority visible."""

import copy
import hashlib
import json
import pathlib
import runpy
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-experiment-contract"
KERNEL = runpy.run_path(str(SKILL / "kernel.py"))
EXAMPLE = json.loads((SKILL / "assets" / "example.json").read_text(encoding="utf-8"))


class ExperimentContractTests(unittest.TestCase):
    def test_complete_synthetic_draft_stays_unregistered(self):
        report = KERNEL["szl_draft_experiment_contract"](copy.deepcopy(EXAMPLE))
        self.assertEqual(report["state"], "DRAFT_READY_FOR_REVIEW")
        self.assertEqual(report["review_questions"], [])
        self.assertEqual(report["unit_relation"], "SAME_UNIT_DECLARED_NOT_VALIDATED")
        self.assertEqual(report["analysis_plan_handoff"]["state"], "NOT_FROZEN")
        self.assertEqual(report["analysis_plan_handoff"]["candidate_fields"]["sample_unit"], "series")
        self.assertEqual(report["preregistration"], "NOT_REGISTERED")
        self.assertEqual(report["experiment_execution"], "NOT_PERFORMED")
        self.assertEqual(report["scientific_efficacy"], "NOT_EVALUATED")
        expected = hashlib.sha256(KERNEL["szl_contract_bytes"](EXAMPLE)).hexdigest()
        self.assertEqual(report["input_sha256"], expected)

    def test_missing_control_falsifier_and_assumption_check_ask_researcher(self):
        payload = copy.deepcopy(EXAMPLE)
        payload["fields"]["control"] = None
        payload["fields"]["falsifier"] = None
        payload["assumptions"][0]["check"] = None
        report = KERNEL["szl_draft_experiment_contract"](payload)
        self.assertEqual(report["state"], "NEEDS_RESEARCHER_INPUT")
        self.assertEqual({item["field"] for item in report["review_questions"]},
                         {"control", "falsifier", "assumptions.independent-series.check"})
        self.assertEqual(report["experiment_execution"], "NOT_PERFORMED")

    def test_unit_mismatch_requires_plan_and_withholds_audit_unit_mapping(self):
        payload = copy.deepcopy(EXAMPLE)
        payload["fields"]["allocation_unit"] = "site"
        report = KERNEL["szl_draft_experiment_contract"](payload)
        self.assertEqual(report["state"], "NEEDS_RESEARCHER_INPUT")
        self.assertIn("dependence_plan", [item["field"] for item in report["review_questions"]])
        self.assertEqual(report["unit_relation"], "UNRESOLVED")
        self.assertIsNone(report["analysis_plan_handoff"]["candidate_fields"]["sample_unit"])
        payload["fields"]["dependence_plan"] = "Model site clustering and keep sites together in the split."
        report = KERNEL["szl_draft_experiment_contract"](payload)
        self.assertEqual(report["state"], "DRAFT_READY_FOR_REVIEW")
        self.assertEqual(report["unit_relation"], "DEPENDENCE_PLAN_DECLARED_NOT_VALIDATED")
        self.assertEqual(report["analysis_plan_handoff"]["unit_mapping"], "UNRESOLVED")

    def test_rejects_extra_fields_duplicate_keys_and_nonfinite_json(self):
        payload = copy.deepcopy(EXAMPLE)
        payload["fields"]["result"] = "already proven"
        with self.assertRaisesRegex(ValueError, "exact experiment-contract"):
            KERNEL["szl_draft_experiment_contract"](payload)
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":"\\ud800"}', b'[' * 17 + b'0' + b']' * 17):
            with self.subTest(raw=raw[:20]), self.assertRaises((ValueError, UnicodeError)):
                KERNEL["read_json"](raw)

    def test_cli_refuses_overwrite_and_reports_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "input.json"
            output = root / "output.json"
            source.write_text(json.dumps(EXAMPLE), encoding="utf-8")
            command = [sys.executable, "-B", str(SKILL / "scripts" / "run.py"), str(source), "--output", str(output)]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["state"], "DRAFT_READY_FOR_REVIEW")
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(second.returncode, 2)
            self.assertIn("INVALID_INPUT", second.stderr)
            incomplete = copy.deepcopy(EXAMPLE)
            incomplete["fields"]["harm_limit"] = None
            source.write_text(json.dumps(incomplete), encoding="utf-8")
            third = subprocess.run(command[:4], capture_output=True, text=True)
            self.assertEqual(third.returncode, 1, third.stderr)
            self.assertEqual(json.loads(third.stdout)["state"], "NEEDS_RESEARCHER_INPUT")


if __name__ == "__main__":
    unittest.main()
