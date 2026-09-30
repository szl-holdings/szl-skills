"""Paired protocol behavioral checks with explicitly synthetic measurements."""
import copy
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER = ROOT / "skills/szl-paired-science/scripts/qualify.py"
SPEC = importlib.util.spec_from_file_location("paired_science", HELPER)
QUALIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QUALIFY)


def experiment(count=5):
    return {"schema": "szl.paired-science/v1", "source_revision": "1" * 40,
            "dataset_sha256": "2" * 64, "plan_sha256": "3" * 64,
            "input_scope": "SYNTHETIC", "pre_registered": True, "independent_pairs": True,
            "alpha": 0.05, "minimum_normalized_improvement": 0.2,
            "identity_control_tolerance": 0, "train_ids": ["train-a"], "test_ids": ["test-a"],
            "tasks": [{"id": "toy", "loss_unit": "meters", "training_scale": 2,
                       "scale_scope": "TRAIN_ONLY", "expected_trial_ids": [str(i) for i in range(count)],
                       "trials": [{"id": str(i), "baseline_loss": 2, "treatment_loss": 1,
                                   "identity_control_loss": 2, "baseline_predictions_sha256": "4" * 64,
                                   "treatment_predictions_sha256": "5" * 64,
                                   "identity_control_predictions_sha256": "4" * 64} for i in range(count)]}]}


class PairedScienceTests(unittest.TestCase):
    def invalid(self, plan):
        with self.assertRaises(QUALIFY.ProtocolError):
            QUALIFY.qualify(plan)

    def test_exact_sign_flip_all_positive_and_ties(self):
        self.assertEqual(QUALIFY.sign_flip_p([1] * 5), 1 / 32)
        self.assertEqual(QUALIFY.sign_flip_p([0] * 5), 1)
        self.assertEqual(QUALIFY.sign_flip_p([1, 0, 0]), 0.5)

    def test_synthetic_qualified_with_no_production_authority(self):
        report = QUALIFY.qualify(experiment())
        self.assertEqual(report["status"], "QUALIFIED_LOCAL_COMPARISON")
        self.assertEqual(report["production_authority"], "NONE")
        self.assertFalse(report["declarations_independently_verified"])

    def test_units_rescaling_preserves_dimensionless_effect(self):
        plan = experiment()
        before = QUALIFY.qualify(plan)["tasks"][0]
        plan["tasks"][0]["training_scale"] *= 1000
        for trial in plan["tasks"][0]["trials"]:
            for key in ("baseline_loss", "treatment_loss", "identity_control_loss"):
                trial[key] *= 1000
        after = QUALIFY.qualify(plan)["tasks"][0]
        self.assertEqual(before, after)

    def test_missing_duplicate_or_substituted_pair_is_invalid(self):
        for mode in ("missing", "duplicate", "unexpected"):
            plan = experiment()
            trials = plan["tasks"][0]["trials"]
            if mode == "missing":
                trials.pop()
            else:
                trials[-1]["id"] = trials[0]["id"] if mode == "duplicate" else "unknown"
            self.invalid(plan)

    def test_test_leakage_and_non_train_scale_invalid(self):
        plan = experiment()
        plan["test_ids"] = plan["train_ids"][:]
        self.invalid(plan)
        plan = experiment()
        plan["tasks"][0]["scale_scope"] = "ALL_DATA"
        self.invalid(plan)

    def test_failed_control_cannot_qualify(self):
        for field, value in (("identity_control_loss", 1), ("identity_control_predictions_sha256", "6" * 64)):
            plan = experiment()
            plan["tasks"][0]["trials"][0][field] = value
            report = QUALIFY.qualify(plan)
            self.assertEqual(report["status"], "REJECTED_LOCAL_COMPARISON")
            self.assertFalse(report["tasks"][0]["identity_control_passed"])

    def test_no_effect_or_small_effect_rejected(self):
        for loss in (2, 1.9, 3):
            plan = experiment()
            for trial in plan["tasks"][0]["trials"]:
                trial["treatment_loss"] = loss
            self.assertEqual(QUALIFY.qualify(plan)["status"], "REJECTED_LOCAL_COMPARISON")

    def test_multiple_tasks_corrected_without_mixed_unit_average(self):
        plan = experiment()
        second = copy.deepcopy(plan["tasks"][0])
        second.update(id="second", loss_unit="dollars")
        plan["tasks"].append(second)
        report = QUALIFY.qualify(plan)
        self.assertEqual(report["status"], "REJECTED_LOCAL_COMPARISON")
        self.assertEqual(report["tasks"][0]["bonferroni_p"], 1 / 16)
        self.assertIsNone(report["raw_cross_task_average"])

    def test_correlation_or_posthoc_plan_cannot_qualify(self):
        for field in ("independent_pairs", "pre_registered"):
            plan = experiment()
            plan[field] = False
            self.assertEqual(QUALIFY.qualify(plan)["status"], "REJECTED_LOCAL_COMPARISON")

    def test_nonfinite_zero_boolean_and_budget_invalid(self):
        for value in (float("nan"), float("inf"), 0, True):
            plan = experiment()
            plan["tasks"][0]["training_scale"] = value
            self.invalid(plan)
        self.invalid(experiment(17))
        self.invalid(experiment(2))

    def test_duplicate_json_nonfinite_and_size_rejected(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'x' * (QUALIFY.MAX_BYTES + 1)):
            with self.assertRaises(ValueError):
                QUALIFY.load_bytes(raw)

    def test_cli_identifies_actual_input_and_helper_bytes(self):
        import hashlib
        import json
        raw = json.dumps(experiment()).encode()
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "experiment.json"
            path.write_bytes(raw)
            run = subprocess.run([sys.executable, "-B", str(HELPER), str(path)], capture_output=True, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(report["input_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(report["helper_sha256"], hashlib.sha256(HELPER.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
