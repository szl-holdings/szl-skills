# SPDX-License-Identifier: Apache-2.0
"""Original deterministic acceptance cases; synthetic evidence, no subprocesses."""
import contextlib
import copy
import hashlib
import io
import json
import pathlib
import runpy
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATASET = ROOT / "skills" / "szl-dataset-readiness"
PAIRED = ROOT / "skills" / "szl-paired-science"


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def artifact(text):
    return {"utf8": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def json_artifact(value):
    return artifact(encoded(value))


def hash_object(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


def experiment_v2(input_text="synthetic evaluation signal", target_value=0, corpus_text="Synthetic corpus fixture; no external scientific evidence."):
    """Standalone valid synthetic fixture; builder does not call the implementation."""
    cases = [{"id": "train-a", "input": artifact("synthetic training signal"), "target": artifact("0")},
             {"id": "test-a", "input": artifact(input_text), "target": artifact(encoded(target_value))}]
    corpus = artifact(corpus_text)
    scorer = json_artifact({"schema": "szl.scorer/v1", "tasks": [{"task_id": "toy", "metric": "mae", "loss_unit": "meters"}]})
    normalization = json_artifact({"schema": "szl.normalization/v1", "tasks": [{"task_id": "toy", "records": [{"case_id": "train-a", "prediction": 2}]}]})
    case_hashes = [{"case_id": c["id"], "input_sha256": c["input"]["sha256"], "target_sha256": c["target"]["sha256"]}
                   for c in sorted(cases, key=lambda c: c["id"])]
    digests = {"corpus_sha256": corpus["sha256"], "scorer_sha256": scorer["sha256"],
               "normalization_sha256": normalization["sha256"], "case_bindings_sha256": hash_object(case_hashes)}
    prediction_digests = {k: v for k, v in digests.items() if k != "case_bindings_sha256"}
    for name in ("input", "target"):
        prediction_digests[name + "_sha256"] = hash_object([{"case_id": "test-a", "sha256": cases[1][name]["sha256"]}])
    trials, trial_bindings = [], []
    for i in range(5):
        trial_id = str(i)
        envelopes = {}
        for role, value in (("baseline", target_value + 2), ("treatment", target_value + 1), ("identity_control", target_value + 2)):
            envelope = {"schema": "szl.predictions/v1", "task_id": "toy", "trial_id": trial_id,
                        "predictions": [{"case_id": "test-a", "value": value}], **prediction_digests}
            envelopes[role] = json_artifact(envelope)
        trials.append({"id": trial_id, "baseline_loss": 2, "treatment_loss": 1, "identity_control_loss": 2,
                       **{role + "_predictions_sha256": obj["sha256"] for role, obj in envelopes.items()}})
        trial_bindings.append({"task_id": "toy", "trial_id": trial_id, **envelopes})
    task = {"id": "toy", "loss_unit": "meters", "training_scale": 2, "scale_scope": "TRAIN_ONLY",
            "expected_trial_ids": [str(i) for i in range(5)], "trials": trials}
    plan = {"schema": "szl.paired-science/v2", "source_revision": "1" * 40,
            "dataset_sha256": corpus["sha256"], "plan_sha256": "0" * 64,
            "input_scope": "SYNTHETIC", "pre_registered": True, "independent_pairs": True,
            "alpha": 0.05, "minimum_normalized_improvement": 0.2, "identity_control_tolerance": 0,
            "train_ids": ["train-a"], "test_ids": ["test-a"], "tasks": [task]}
    projection = {k: plan[k] for k in ("source_revision", "input_scope", "pre_registered", "independent_pairs",
                                      "alpha", "minimum_normalized_improvement", "identity_control_tolerance")}
    projection.update(schema="szl.paired-plan/v1", train_ids=["train-a"], test_ids=["test-a"], artifact_digests=digests,
                      tasks=[{k: task[k] for k in ("id", "loss_unit", "training_scale", "scale_scope", "expected_trial_ids")}])
    prespecified = json_artifact(projection)
    plan["plan_sha256"] = prespecified["sha256"]
    plan["bindings"] = {"schema": "szl.paired-bindings/v1", "cases": cases, "corpus": corpus, "scorer": scorer,
                        "normalization": normalization, "plan": prespecified, "trials": trial_bindings}
    return plan


class DatasetLeakageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = staticmethod(runpy.run_path(str(DATASET / "kernel.py"))["szl_audit_dataset"])

    def setUp(self):
        self.payload = json.loads((DATASET / "assets" / "leakage-clean.json").read_text(encoding="utf-8"))

    def test_complete_clean_evidence_keeps_review_boundary(self):
        original = copy.deepcopy(self.payload)
        report = self.audit(**self.payload)
        self.assertEqual(report["status"], "NO_CHECKED_ISSUES")
        self.assertTrue(all(c["status"] == "NO_CHECKED_ISSUES" for c in report["leakage_checks"].values()))
        self.assertEqual(report["readiness"], "REVIEW")
        self.assertFalse(report["provenance_verified"])
        self.assertEqual(self.payload, original)

    def test_entity_overlap_with_distinct_feature_values(self):
        self.payload["rows"][1]["entity_id"] = "synthetic-A"
        report = self.audit(**self.payload)
        self.assertIn("CROSS_SPLIT_ENTITY_LEAKAGE", report["issues"])
        self.assertFalse(report["feature_leakage"])

    def test_training_label_unavailable_at_evaluation_origin(self):
        self.payload["rows"][0]["label_available_at"] = "2026-01-05T00:00:00Z"
        self.assertIn("TRAIN_LABEL_AVAILABILITY_LEAKAGE", self.audit(**self.payload)["issues"])

    def test_future_observation_and_reversed_order_detected(self):
        self.payload["rows"][0]["observation_time"] = "2026-01-05T00:00:00Z"
        issues = self.audit(**self.payload)["issues"]
        self.assertIn("FUTURE_OBSERVATION_LEAKAGE", issues)
        self.assertIn("TEMPORAL_SPLIT_ORDER_LEAKAGE", issues)

    def test_timezone_offsets_order_as_utc(self):
        self.payload["rows"][1]["observation_time"] = "2026-01-03T02:00:00+02:00"
        self.assertEqual(self.audit(**self.payload)["leakage_checks"]["temporal"]["status"], "NO_CHECKED_ISSUES")

    def test_ambiguous_time_is_unknown_and_not_imputed(self):
        self.payload["rows"][1]["prediction_time"] = "2026-01-03T01:00:00"
        report = self.audit(**self.payload)
        self.assertEqual(report["leakage_checks"]["temporal"]["status"], "UNKNOWN")
        self.assertIn("LEAKAGE_EVIDENCE_INCOMPLETE", report["issues"])

    def test_utc_normalization_underflow_is_incomplete_evidence(self):
        self.payload["rows"][1]["prediction_time"] = "0001-01-01T00:00:00+23:59"
        report = self.audit(**self.payload)
        check = report["leakage_checks"]["temporal"]
        self.assertEqual(check["status"], "UNKNOWN")
        self.assertIn("LEAKAGE_EVIDENCE_INCOMPLETE", report["issues"])
        self.assertTrue(any(isinstance(item, dict) and item.get("row_index") == 1
                            and item.get("column") == "prediction_time"
                            and item.get("reason") == "MISSING_AMBIGUOUS_OR_OUT_OF_RANGE_TIME"
                            for item in check["missing_evidence"]))

    def test_utc_overflow_is_unknown_and_adjacent_representable_edges_normalize(self):
        self.payload["rows"][1]["prediction_time"] = "9999-12-31T23:59:59-23:59"
        report = self.audit(**self.payload)
        self.assertEqual(report["leakage_checks"]["temporal"]["status"], "UNKNOWN")
        self.assertIn("LEAKAGE_EVIDENCE_INCOMPLETE", report["issues"])
        stamp = runpy.run_path(str(DATASET / "kernel.py"))["szl_dataset_stamp"]
        for value, expected in (("0001-01-01T01:00:00+01:00", "0001-01-01T00:00:00+00:00"),
                                ("9999-12-31T22:59:59-01:00", "9999-12-31T23:59:59+00:00")):
            with self.subTest(value=value):
                self.assertEqual(stamp(value).isoformat(), expected)

    def test_fit_heldout_rows_cannot_hide_behind_train_declaration(self):
        self.payload["leakage_spec"]["transforms"][0]["fit_row_ids"] = ["test-a"]
        report = self.audit(**self.payload)
        self.assertIn("PREPROCESSING_FIT_SCOPE_LEAKAGE", report["issues"])
        self.assertIn("FIT_SPLIT_DECLARATION_MISMATCH", report["issues"])

    def test_transform_cannot_declare_heldout_scope_allowed(self):
        transform = self.payload["leakage_spec"]["transforms"][0]
        transform.update(fit_row_ids=["test-a"], fit_split_ids=["test"], allowed_fit_splits=["train", "test"])
        self.assertIn("PREPROCESSING_FIT_SCOPE_LEAKAGE", self.audit(**self.payload)["issues"])

    def test_unresolved_fit_row_and_omitted_transform_are_unknown(self):
        self.payload["leakage_spec"]["transforms"][0]["fit_row_ids"] = ["not-selected"]
        report = self.audit(**self.payload)
        check = report["leakage_checks"]["preprocessing"]
        self.assertEqual(check["status"], "UNKNOWN")
        self.assertTrue(check["missing_evidence"])
        self.assertEqual(check["findings"], [])
        self.assertNotIn("FIT_SPLIT_DECLARATION_MISMATCH", report["issues"])
        self.payload["leakage_spec"]["transforms"] = []
        self.assertEqual(self.audit(**self.payload)["leakage_checks"]["preprocessing"]["status"], "UNKNOWN")

    def test_partial_fit_evidence_retains_observed_heldout_contradiction(self):
        self.payload["leakage_spec"]["transforms"][0]["fit_row_ids"] = ["test-a", "not-selected"]
        report = self.audit(**self.payload)
        check = report["leakage_checks"]["preprocessing"]
        self.assertEqual(check["status"], "ISSUES_FOUND")
        self.assertTrue(check["missing_evidence"])
        self.assertIn("PREPROCESSING_FIT_SCOPE_LEAKAGE", report["issues"])
        self.assertIn("FIT_SPLIT_DECLARATION_MISMATCH", report["issues"])

    def test_invalid_typed_and_duplicate_evidence_rejected(self):
        for mode in ("entity_bool", "duplicate_id", "time_schema", "nan", "split_list", "metadata_bool"):
            with self.subTest(mode=mode):
                payload = copy.deepcopy(self.payload)
                if mode == "entity_bool":
                    payload["rows"][0]["entity_id"] = True
                elif mode == "duplicate_id":
                    payload["rows"][1]["case_id"] = "train-a"
                elif mode == "time_schema":
                    payload["leakage_spec"]["temporal"]["extra"] = 1
                elif mode == "nan":
                    payload["rows"][0]["signal"] = float("nan")
                elif mode == "split_list":
                    payload["leakage_spec"]["temporal"]["training_splits"] = True
                else:
                    payload["metadata"]["license"] = True
                with self.assertRaises(ValueError):
                    self.audit(**payload)

    def test_missing_declarations_and_legacy_not_checked(self):
        self.payload["leakage_spec"] = {"schema": "szl.dataset-leakage/v1", "row_id_column": "case_id"}
        self.assertTrue(all(c["status"] == "UNKNOWN" for c in self.audit(**self.payload)["leakage_checks"].values()))
        del self.payload["leakage_spec"]
        self.assertTrue(all(c["status"] == "NOT_CHECKED" for c in self.audit(**self.payload)["leakage_checks"].values()))

    def test_declaration_budgets_bound_nested_work(self):
        for field, value in (("feature_columns", ["x" + str(i) for i in range(65)]),
                             ("entity_columns", ["e" + str(i) for i in range(33)]),
                             ("split_order", ["s" + str(i) for i in range(33)])):
            payload = copy.deepcopy(self.payload)
            if field == "feature_columns":
                payload[field] = value
            elif field == "entity_columns":
                payload["leakage_spec"][field] = value
            else:
                payload["leakage_spec"]["temporal"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.audit(**payload)


class PairedBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = runpy.run_path(str(PAIRED / "scripts" / "qualify.py"))

    def qualify(self, plan):
        return self.module["qualify"](plan, expected_manifest_sha256=self.frozen_manifest)

    def setUp(self):
        self.plan = experiment_v2()
        self.frozen_manifest = hash_object(json.loads(self.plan["bindings"]["plan"]["utf8"])["artifact_digests"])

    def test_valid_bound_fixture_readbacks_before_local_inference(self):
        original = copy.deepcopy(self.plan)
        report = self.qualify(self.plan)
        self.assertEqual(report["binding_status"], "VERIFIED_INLINE_BYTES")
        self.assertEqual(report["status"], "QUALIFIED_LOCAL_COMPARISON")
        self.assertEqual(report["tasks"][0]["sign_flip_p"], 1 / 32)
        self.assertFalse(report["consumption_verified"])
        self.assertEqual(report["production_authority"], "NONE")
        self.assertEqual(self.plan, original)

    def test_changed_input_target_or_corpus_cannot_reuse_old_envelopes(self):
        for component in ("input", "target", "corpus"):
            with self.subTest(component=component):
                plan = copy.deepcopy(self.plan)
                if component == "corpus":
                    plan["bindings"]["corpus"] = artifact("Changed synthetic corpus.")
                else:
                    plan["bindings"]["cases"][1][component] = artifact("1" if component == "target" else "Changed input")
                report = self.qualify(plan)
                self.assertEqual(report["status"], "REJECTED_LOCAL_COMPARISON")
                self.assertEqual(report["statistical_evaluation"], "NOT_RUN")
                self.assertTrue(any(component.upper() in f for f in report["binding_evidence"]["findings"]))

    def test_fully_rehashed_payload_cannot_own_frozen_input_truth(self):
        for changed in (experiment_v2(input_text="replaced evidence text"), experiment_v2(target_value=1),
                        experiment_v2(corpus_text="replaced corpus")):
            report = self.qualify(changed)
            self.assertIn("FROZEN_MANIFEST_MISMATCH", report["binding_evidence"]["findings"])
            self.assertEqual(report["statistical_evaluation"], "NOT_RUN")

    def test_v2_without_separate_frozen_manifest_remains_declared(self):
        report = self.module["qualify"](self.plan)
        self.assertEqual(report["binding_status"], "DECLARED")
        self.assertEqual(report["status"], "REJECTED_LOCAL_COMPARISON")

    def test_raw_digest_mismatch_and_absent_bytes_block(self):
        self.plan["bindings"]["corpus"]["utf8"] += " altered"
        self.assertEqual(self.qualify(self.plan)["binding_status"], "MISMATCH")
        self.plan = experiment_v2()
        self.plan["bindings"]["cases"][1]["input"]["utf8"] = None
        report = self.qualify(self.plan)
        self.assertEqual(report["binding_status"], "DECLARED")
        self.assertEqual(report["tasks"], [])

    def test_false_loss_or_scale_cannot_qualify(self):
        for mode in ("loss", "scale"):
            plan = copy.deepcopy(self.plan)
            if mode == "loss":
                plan["tasks"][0]["trials"][0]["treatment_loss"] = 0.5
            else:
                plan["tasks"][0]["training_scale"] = 4
            report = self.qualify(plan)
            self.assertEqual(report["status"], "REJECTED_LOCAL_COMPARISON")
            self.assertTrue(any("READBACK_MISMATCH" in f for f in report["binding_evidence"]["findings"]))

    def test_duplicate_substituted_or_missing_pair_evidence_invalid(self):
        for mode in ("case", "trial", "prediction_case", "missing_trial", "normalization_test"):
            plan = copy.deepcopy(self.plan)
            if mode == "case":
                plan["bindings"]["cases"][1]["id"] = "train-a"
            elif mode == "trial":
                plan["bindings"]["trials"][1]["trial_id"] = "0"
            elif mode == "missing_trial":
                plan["bindings"]["trials"].pop()
            elif mode == "prediction_case":
                obj = json.loads(plan["bindings"]["trials"][0]["treatment"]["utf8"])
                obj["predictions"][0]["case_id"] = "other-case"
                plan["bindings"]["trials"][0]["treatment"] = json_artifact(obj)
            else:
                obj = json.loads(plan["bindings"]["normalization"]["utf8"])
                obj["tasks"][0]["records"][0]["case_id"] = "test-a"
                plan["bindings"]["normalization"] = json_artifact(obj)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.qualify(plan)

    def test_scorer_and_prespecified_plan_changes_block(self):
        for name in ("scorer", "plan"):
            plan = copy.deepcopy(self.plan)
            obj = json.loads(plan["bindings"][name]["utf8"])
            if name == "scorer":
                obj["tasks"][0]["metric"] = "mse"
            else:
                obj["alpha"] = 0.1
            plan["bindings"][name] = json_artifact(obj)
            self.assertEqual(self.qualify(plan)["status"], "REJECTED_LOCAL_COMPARISON")

    def test_invalid_bool_nonfinite_schema_and_missing_byte_types(self):
        for mode in ("alpha", "nan", "schema", "utf8", "target_bool"):
            plan = copy.deepcopy(self.plan)
            if mode == "alpha":
                plan["alpha"] = True
            elif mode == "nan":
                plan["tasks"][0]["trials"][0]["baseline_loss"] = float("nan")
            elif mode == "schema":
                plan["bindings"]["extra"] = 1
            elif mode == "utf8":
                plan["bindings"]["corpus"]["utf8"] = 5
            else:
                plan["bindings"]["cases"][1]["target"] = artifact("true")
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.qualify(plan)

    def test_absent_bindings_rejects_and_legacy_remains_declaration_only(self):
        self.plan["bindings"] = None
        self.assertEqual(self.qualify(self.plan)["status"], "REJECTED_LOCAL_COMPARISON")
        del self.plan["bindings"]
        self.plan["schema"] = "szl.paired-science/v1"
        report = self.qualify(self.plan)
        self.assertEqual(report["status"], "QUALIFIED_LOCAL_COMPARISON")
        self.assertEqual(report["binding_status"], "DECLARED")

    def test_cli_invalid_json_returns_nonzero_without_process_launch(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[' * 65 + b'0' + b']' * 65):
            with self.subTest(raw=raw[:20]), tempfile.TemporaryDirectory() as directory:
                path = pathlib.Path(directory) / "invalid.json"
                path.write_bytes(raw)
                output = io.StringIO()
                with mock.patch.object(sys, "argv", ["qualify.py", str(path)]), contextlib.redirect_stdout(output):
                    code = self.module["main"]()
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(output.getvalue())["status"], "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main()
