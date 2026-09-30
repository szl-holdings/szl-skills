"""Independent synthetic forward-use review; no models or experiments are run.

Chosen request: prepare a reviewable offline handoff for an invented sensor study.
These tests use only public local helpers, retained invented bytes, stdlib and runpy.
They do not import author tests, use subprocesses, access credentials, or use networks.
Scientific/model/agent performance remains NOT_MEASURED in every scenario.
"""
import copy
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def object_digest(value):
    return digest(canonical(value))


def module(skill, relative="kernel.py"):
    return runpy.run_path(str(ROOT / "skills" / ("szl-" + skill) / relative),
                          run_name="independent_forward_review")


def inline(value):
    text = canonical(value).decode("utf-8")
    return {"utf8": text, "sha256": digest(text)}


def bound_artifact(name, text):
    return {"id": name, "content_utf8": text, "sha256": digest(text)}


def paired_fixture():
    """Invented saved predictions, with independently calculated byte identities."""
    train_ids, test_ids = ["train-a", "train-b"], ["test-a", "test-b"]
    values = {"train-a": 10, "train-b": 20, "test-a": 30, "test-b": 40}
    cases = [{"id": key, "input": inline({"sensor": key, "time": 0}),
              "target": inline(values[key])} for key in sorted(values)]
    corpus = inline({"description": "Invented sensor records; no external dataset",
                     "ids": sorted(values)})
    scorer = inline({"schema": "szl.scorer/v1", "tasks": [
        {"task_id": "signal", "metric": "mae", "loss_unit": "m"}]})
    normalization = inline({"schema": "szl.normalization/v1", "tasks": [
        {"task_id": "signal", "records": [
            {"case_id": key, "prediction": values[key] + 2} for key in train_ids]}]})
    trial_ids = ["saved-run-" + str(index) for index in range(6)]
    task = {"id": "signal", "loss_unit": "m", "training_scale": 2,
            "scale_scope": "TRAIN_ONLY", "expected_trial_ids": trial_ids, "trials": []}
    plan = {"schema": "szl.paired-science/v2", "source_revision": "1" * 40,
            "dataset_sha256": corpus["sha256"], "plan_sha256": "0" * 64,
            "input_scope": "SYNTHETIC", "pre_registered": True,
            "independent_pairs": True, "alpha": 0.05,
            "minimum_normalized_improvement": 0.1, "identity_control_tolerance": 0,
            "train_ids": train_ids, "test_ids": test_ids, "tasks": [task]}
    case_hashes = [{"case_id": item["id"], "input_sha256": item["input"]["sha256"],
                    "target_sha256": item["target"]["sha256"]} for item in cases]
    pins = {"corpus_sha256": corpus["sha256"], "scorer_sha256": scorer["sha256"],
            "normalization_sha256": normalization["sha256"],
            "case_bindings_sha256": object_digest(case_hashes)}
    projection = {field: plan[field] for field in (
        "source_revision", "input_scope", "pre_registered", "independent_pairs",
        "alpha", "minimum_normalized_improvement", "identity_control_tolerance")}
    projection.update(schema="szl.paired-plan/v1", train_ids=sorted(train_ids),
                      test_ids=sorted(test_ids), artifact_digests=copy.deepcopy(pins),
                      tasks=[{field: task[field] for field in (
                          "id", "loss_unit", "training_scale", "scale_scope",
                          "expected_trial_ids")}])
    frozen = inline(projection)
    plan["plan_sha256"] = frozen["sha256"]
    pins.update(input_sha256=object_digest([
        {"case_id": item["id"], "sha256": item["input"]["sha256"]}
        for item in cases if item["id"] in test_ids]),
        target_sha256=object_digest([
            {"case_id": item["id"], "sha256": item["target"]["sha256"]}
            for item in cases if item["id"] in test_ids]))
    retained_trials = []
    for index, trial_id in enumerate(trial_ids):
        # Integer offsets avoid a declaration/readback discrepancy from binary rounding.
        baseline_loss, treatment_loss = index + 2, index + 1
        def envelope(offset):
            return inline({"schema": "szl.predictions/v1", "task_id": "signal",
                           "trial_id": trial_id,
                           **{key: pins[key] for key in (
                               "input_sha256", "target_sha256", "corpus_sha256",
                               "scorer_sha256", "normalization_sha256")},
                           "predictions": [{"case_id": key, "value": values[key] + offset}
                                           for key in sorted(test_ids)]})
        baseline, treatment = envelope(baseline_loss), envelope(treatment_loss)
        task["trials"].append({"id": trial_id, "baseline_loss": baseline_loss,
                               "treatment_loss": treatment_loss,
                               "identity_control_loss": baseline_loss,
                               "baseline_predictions_sha256": baseline["sha256"],
                               "treatment_predictions_sha256": treatment["sha256"],
                               "identity_control_predictions_sha256": baseline["sha256"]})
        retained_trials.append({"task_id": "signal", "trial_id": trial_id,
                                "baseline": baseline, "treatment": treatment,
                                "identity_control": copy.deepcopy(baseline)})
    plan["bindings"] = {"schema": "szl.paired-bindings/v1", "cases": cases,
                        "corpus": corpus, "scorer": scorer, "normalization": normalization,
                        "plan": frozen, "trials": retained_trials}
    # This separate value models custody of a frozen manifest; it is not a payload field.
    return plan, object_digest(projection["artifact_digests"])


def control_fixture():
    protocol = bound_artifact("protocol", "Synthetic negative outcome: fixed absolute delta.\n")
    scorer = bound_artifact("scorer", '{"metric":"mean_delta","comparator":"sham"}\n')
    execution = bound_artifact("execution", '{"scope":"COMPUTATIONAL","seed":11}\n')
    selected_input = bound_artifact("input", "Invented signal and placebo readouts.\n")
    registry = {"id": "sensor-null", "version": 1, "scope": "COMPUTATIONAL",
                "frozen_at": "2026-09-30T08:00:00Z",
                "graph": {"nodes": ["batch", "algorithm", "signal", "placebo"],
                          "edges": [["batch", "algorithm"], ["batch", "signal"], ["batch", "placebo"],
                                    ["algorithm", "signal"]]},
                "main": {"intervention": "algorithm", "readout": "signal"},
                "protocol_artifact": "protocol", "scorer_artifact": "scorer",
                "execution_artifact": "execution", "controls": [
                    {"id": "placebo-readout", "kind": "NEGATIVE_OUTCOME",
                     "intervention": "algorithm", "readout": "placebo",
                     "shared_nuisance": ["batch"],
                     "rationale": "Batch affects algorithm selection and both outputs; algorithm has no placebo path.",
                     "input_artifact": "input", "metric": "mean_delta",
                     "expectation": "ABS_DELTA_LE", "tolerance": 0.01}]}
    outcome = {"schema": "szl.negative-control-result.v1", "control_id": "placebo-readout",
               "metric": "mean_delta", "registry_sha256": object_digest(registry),
               "protocol_sha256": protocol["sha256"], "scorer_sha256": scorer["sha256"],
               "execution_sha256": execution["sha256"], "input_sha256": selected_input["sha256"],
               "tolerance": 0.01, "delta": 0.005,
               "started_at": "2026-09-30T09:00:00Z", "completed_at": "2026-09-30T09:01:00Z",
               "status": "SUCCESS", "failure_reason": None, "contaminated": False}
    observed = bound_artifact("outcome", canonical(outcome).decode("utf-8"))
    payload = {"schema": "szl.negative-control-audit.v1", "registry": registry,
               "artifacts": [protocol, scorer, execution, selected_input, observed],
               "results": [{"control_id": "placebo-readout", "outcome_artifact": "outcome"}]}
    return payload


def plan_fixture():
    hypothesis = {"id": "forecast-error", "statement": "The saved candidate lowers held-out MAE.",
                  "primary_metric": "mae", "direction": "LOWER",
                  "estimand": "Mean paired improvement over independent synthetic runs",
                  "sample_unit": "run", "group_unit": "sensor", "split_sha256": digest("train/test v1"),
                  "alpha": 0.05, "family": "forecast", "practical_margin": 0.1,
                  "exclusions": []}
    plan = {"id": "sensor-plan", "version": 1, "frozen_at": "2026-09-30T08:00:00Z",
            "hypotheses": [hypothesis],
            "families": [{"id": "forecast", "method": "NONE", "alpha": 0.05, "size": 1}],
            "stopping_rule": {"kind": "FIXED_ATTEMPTS", "maximum_attempts": 2},
            "scheduled_attempts": [{"id": "run-a", "hypothesis_id": "forecast-error"},
                                   {"id": "run-b", "hypothesis_id": "forecast-error"}]}
    run = {"plan_id": "sensor-plan", "plan_version": 1, "plan_sha256": object_digest(plan),
           "started_at": "2026-09-30T09:00:00Z", "completed_at": "2026-09-30T09:10:00Z",
           "precommit_declared": True, "hypotheses": copy.deepcopy(plan["hypotheses"]),
           "families": copy.deepcopy(plan["families"]),
           "stopping_rule": copy.deepcopy(plan["stopping_rule"]),
           "attempts": [{"id": "run-a", "hypothesis_id": "forecast-error", "status": "SUCCESS",
                         "recorded_at": "2026-09-30T09:01:00Z", "result_sha256": digest("saved run a"),
                         "failure_reason": None},
                        {"id": "run-b", "hypothesis_id": "forecast-error", "status": "FAILED",
                         "recorded_at": "2026-09-30T09:02:00Z", "result_sha256": None,
                         "failure_reason": "Synthetic retained parser failure"}]}
    return {"schema": "szl.analysis-plan-audit.v1", "plan": plan, "run": run, "deviations": []}


class ForwardScienceReview(unittest.TestCase):
    evidence = {}

    @classmethod
    def tearDownClass(cls):
        print("FORWARD_REVIEW_EVIDENCE=" + json.dumps(
            {"scope": "Independent synthetic static helper exercise",
             "scientific_performance": "NOT_MEASURED", "model_performance": "NOT_MEASURED",
             "agent_performance": "NOT_MEASURED", "scenarios": cls.evidence},
            sort_keys=True, ensure_ascii=False))

    def record(self, name, details):
        self.evidence[name] = details

    def test_dataset_readiness_retains_split_and_group_findings(self):
        api = module("dataset-readiness")
        rows = [{"row": "train-a", "sensor": "sensor-a", "split": "train", "x": 12, "y": 0},
                {"row": "test-a", "sensor": "sensor-a", "split": "test", "x": 12, "y": 1},
                {"row": "test-b", "sensor": "sensor-b", "split": "test", "x": None, "y": 0}]
        original = copy.deepcopy(rows)
        report = api["szl_audit_dataset"](rows, ["x"], group_column="sensor",
            metadata={"source_revision": "synthetic-v1", "license": "CC0-1.0",
                      "source": "Invented fixture", "collection": "No humans or external records"})
        self.assertEqual(rows, original)
        self.assertTrue({"CROSS_SPLIT_FEATURE_LEAKAGE", "CROSS_SPLIT_GROUP_LEAKAGE",
                         "MISSING_REQUIRED_VALUES"}.issubset(report["issues"]))
        self.assertEqual(report["readiness"], "REVIEW")
        self.assertEqual(report["scientific_suitability"], "NOT_ASSESSED")
        self.assertFalse(report["provenance_verified"])
        self.assertEqual(report["feature_leakage"][0]["row_indexes"], [0, 1])
        clean = api["szl_audit_dataset"](
            [{"x": 1, "split": "train"}, {"x": 2, "split": "test"}], ["x"],
            metadata={"source_revision": "invented-v2", "license": "CC0-1.0"})
        self.assertEqual(clean["status"], "NO_CHECKED_ISSUES")
        self.assertEqual(clean["readiness"], "REVIEW")
        self.record("dataset-readiness", {"status": report["status"], "issues": report["issues"],
            "selected_row_count": report["rows"], "readiness": clean["readiness"],
            "unchecked": "label validity, temporal/semantic leakage and population suitability"})

    def test_dataset_forward_temporal_and_fit_evidence(self):
        api = module("dataset-readiness")
        rows = [
            {"row": "train-1", "sensor": "sensor-a", "split": "train", "x": 1,
             "observed": "2026-09-28T08:00:00Z", "label_available": "2026-09-28T09:00:00Z",
             "prediction": "2026-09-28T08:00:00Z"},
            {"row": "test-1", "sensor": "sensor-b", "split": "test", "x": 2,
             "observed": "2026-09-29T08:00:00Z", "label_available": "2026-09-29T09:00:00Z",
             "prediction": "2026-09-29T08:00:00Z"}]
        spec = {"schema": "szl.dataset-leakage/v1", "row_id_column": "row",
                "entity_columns": ["sensor"],
                "temporal": {"observation_time_column": "observed",
                             "label_available_at_column": "label_available",
                             "prediction_time_column": "prediction", "training_splits": ["train"],
                             "evaluation_splits": ["test"], "split_order": ["train", "test"]},
                "expected_transform_ids": ["scale"], "transforms": [
                    {"id": "scale", "fit_row_ids": ["train-1"], "fit_split_ids": ["train"],
                     "allowed_fit_splits": ["train"]}]}
        clean = api["szl_audit_dataset"](rows, ["x"], leakage_spec=spec)
        self.assertTrue(all(check["status"] == "NO_CHECKED_ISSUES"
                            for check in clean["leakage_checks"].values()))
        heldout_fit = copy.deepcopy(spec)
        heldout_fit["transforms"][0]["fit_row_ids"] = ["test-1"]
        leakage = api["szl_audit_dataset"](rows, ["x"], leakage_spec=heldout_fit)
        self.assertEqual(leakage["leakage_checks"]["preprocessing"]["status"], "ISSUES_FOUND")
        unresolved = copy.deepcopy(spec)
        unresolved["transforms"][0]["fit_row_ids"] = ["not-retained"]
        unknown = api["szl_audit_dataset"](rows, ["x"], leakage_spec=unresolved)
        self.assertEqual(unknown["leakage_checks"]["preprocessing"]["status"], "UNKNOWN")
        self.assertIn("LEAKAGE_EVIDENCE_INCOMPLETE", unknown["issues"])
        future_label = copy.deepcopy(rows)
        future_label[0]["label_available"] = "2026-09-29T10:00:00Z"
        future = api["szl_audit_dataset"](future_label, ["x"], leakage_spec=spec)
        self.assertEqual(future["leakage_checks"]["temporal"]["status"], "ISSUES_FOUND")
        self.record("dataset-leakage-design", {
            "clean_section_statuses": {key: value["status"] for key, value in clean["leakage_checks"].items()},
            "heldout_fit_status": leakage["leakage_checks"]["preprocessing"]["status"],
            "unresolved_fit_status": unknown["leakage_checks"]["preprocessing"]["status"],
            "future_training_label_status": future["leakage_checks"]["temporal"]["status"],
            "fit_execution": "NOT_MEASURED", "semantic_leakage": "NOT_ASSESSED"})

    def test_paired_comparison_binds_retained_predictions_without_model_claim(self):
        api = module("paired-science", "scripts/qualify.py")
        supplied, frozen_manifest = paired_fixture()
        report = api["qualify"](supplied, expected_manifest_sha256=frozen_manifest)
        self.assertEqual(report["binding_status"], "VERIFIED_INLINE_BYTES")
        self.assertEqual(report["status"], "QUALIFIED_LOCAL_COMPARISON")
        self.assertEqual(report["tasks"][0]["sign_flip_p"], 1 / 64)
        self.assertEqual(report["tasks"][0]["mean_normalized_improvement"], 0.5)
        self.assertTrue(report["tasks"][0]["identity_control_passed"])
        self.assertFalse(report["declarations_independently_verified"])
        self.assertFalse(report["consumption_verified"])
        self.assertIsNone(report["raw_cross_task_average"])
        self.assertEqual(report["scientific_performance"], "NOT_MEASURED")
        absent = copy.deepcopy(supplied)
        absent["bindings"] = None
        missing = api["qualify"](absent, expected_manifest_sha256=frozen_manifest)
        self.assertEqual(missing["status"], "REJECTED_LOCAL_COMPARISON")
        self.assertEqual(missing["statistical_evaluation"], "NOT_RUN")
        changed = copy.deepcopy(supplied)
        changed["bindings"]["trials"][0]["treatment"]["utf8"] += " "
        mismatch = api["qualify"](changed, expected_manifest_sha256=frozen_manifest)
        self.assertEqual(mismatch["status"], "REJECTED_LOCAL_COMPARISON")
        missing_lock = api["qualify"](supplied)
        self.assertEqual(missing_lock["status"], "REJECTED_LOCAL_COMPARISON")
        self.assertEqual(missing_lock["statistical_evaluation"], "NOT_RUN")
        self.record("paired-science", {"status": report["status"], "binding": report["binding_status"],
            "missing_byte_status": missing["status"], "changed_byte_status": mismatch["status"],
            "absent_frozen_manifest_status": missing_lock["status"],
            "pairs": 6, "scope": "Invented saved outputs; independence and precommit declared only",
            "model_consumption_verified": report["consumption_verified"]})

    def test_model_attempts_keep_complete_denominator(self):
        api = module("model-evaluation")
        statuses = ["success", "invalid_output", "timeout", "failed", "aborted", "unavailable", "missing"]
        planned = [{"attempt_id": "attempt-" + str(index), "row_id": "row-" + str(index),
                    "label": index % 2} for index in range(len(statuses))]
        observed = []
        for index, status in enumerate(statuses[:-1]):
            record = {"attempt_id": planned[index]["attempt_id"],
                      "row_id": planned[index]["row_id"], "status": status}
            record.update({"probability": 0.1} if status == "success" else
                          {"reason": "Synthetic " + status + " retained in ledger"})
            observed.append(record)
        report = api["szl_audit_attempts"](planned, observed, n_bins=2, threshold=0.5)
        self.assertEqual((report["planned"], report["recorded"], report["attempted"],
                          report["completed"], report["successful"]), (7, 6, 5, 2, 1))
        self.assertEqual(report["planned_accuracy"]["numerator"], 1)
        self.assertEqual(report["planned_accuracy"]["denominator"], 7)
        self.assertEqual(report["probability_metric_coverage"], {"numerator": 1, "denominator": 7})
        self.assertIsNone(report["conditional_probability_metrics"]["auroc"])
        self.assertEqual(report["status"], "INCOMPLETE_ATTEMPT_LEDGER")
        self.assertFalse(report["model_loaded"])
        self.assertEqual(report["prediction_model_binding"], "NOT_VERIFIED")
        self.assertEqual(report["scientific_performance"], "NOT_MEASURED")
        changed_plan = copy.deepcopy(planned)
        changed_plan[0]["label"] = 1
        with self.assertRaises(ValueError):
            api["szl_audit_attempts"](changed_plan, observed,
                                     expected_plan_sha256=report["plan_sha256"])
        self.record("model-evaluation", {"status": report["status"], "planned": report["planned"],
            "recorded": report["recorded"], "attempted": report["attempted"],
            "completed": report["completed"], "successful": report["successful"],
            "status_counts": report["status_counts"], "planned_accuracy": report["planned_accuracy"],
            "conditional_metric_coverage": report["probability_metric_coverage"]})

    def test_capsule_matches_bytes_but_does_not_replay_source(self):
        api = module("reproducibility-capsule")
        # The deliberately inert source is selected and hashed, never executed.
        source = "raise RuntimeError('This source must remain unexecuted')\n"
        contents = {"input.json": canonical({"x": [1, 2]}), "analysis.py": source.encode(),
                    "environment.json": canonical({"python": "3.10+", "packages": []}),
                    "plan.json": canonical({"metric": "sum", "seed": 11}),
                    "output.json": canonical({"sum": 3})}
        roles = {"input.json": "input", "analysis.py": "source",
                 "environment.json": "environment", "plan.json": "analysis_plan",
                 "output.json": "output"}
        replay = {"schema": "szl.offline-replay.v1", "argv": ["python", "analysis.py", "input.json"],
                  "environment": {"path": "environment.json", "sha256": digest(contents["environment.json"])},
                  "analysis_plan": {"path": "plan.json", "sha256": digest(contents["plan.json"])},
                  "seed": 11, "limits": {"network": "denied", "process_spawn": "denied",
                    "secret_access": "denied", "max_seconds": 20, "max_memory_mib": 64},
                  "expected_outputs": [{"path": "output.json", "sha256": digest(contents["output.json"]),
                                        "comparison": {"mode": "exact_bytes"}}]}
        with tempfile.TemporaryDirectory(prefix="science-forward-") as selected:
            base = Path(selected)
            for name, raw in contents.items():
                (base / name).write_bytes(raw)
            capsule = api["szl_make_capsule"](base,
                [{"path": name, "role": roles[name]} for name in contents],
                metadata={"scope": "SYNTHETIC"}, replay=replay)
            report = api["szl_verify_capsule"](base, capsule)
            self.assertEqual(report["integrity"], "MATCH")
            self.assertTrue(report["replay_ready"])
            self.assertEqual(report["execution"], "NOT_RUN")
            self.assertEqual(report["replay_validation"]["capability_denial"], "DECLARED_ONLY")
            self.assertFalse(report["authentic"])
            self.assertFalse(report["scientific_claims_verified"])
            (base / "output.json").write_bytes(canonical({"sum": 4}))
            changed = api["szl_verify_capsule"](base, capsule)
            self.assertEqual(changed["integrity"], "MISMATCH")
            self.assertFalse(changed["replay_ready"])
            with self.assertRaises(ValueError):
                api["szl_make_capsule"](base, ["../input.json"])
        self.record("reproducibility-capsule", {"integrity": report["integrity"],
            "replay_ready": report["replay_ready"], "execution": report["execution"],
            "authentic": report["authentic"], "changed_output_integrity": changed["integrity"],
            "selected_artifacts": sorted(contents)})

    def test_math_claim_records_numerical_scope_and_unmet_proof(self):
        api = module("math-claim-check")
        axes = [0.25, 1.0]
        result = api["szl_weighted_geomean"](axes)
        report = api["szl_check_math_cases"](
            "For x,y in [0,1], their equally weighted geometric mean equals min(x,y).",
            [{"inputs": {"x": axes[0], "y": axes[1]}, "lhs": result, "rhs": min(axes)},
             {"inputs": {"x": 0, "y": 1}, "lhs": 0, "rhs": 0}], atol=0, rtol=0)
        self.assertEqual(report["status"], "NUMERICAL_COUNTEREXAMPLE")
        self.assertEqual(len(report["failures"]), 1)
        self.assertEqual(report["failures"][0]["inputs"], {"x": 0.25, "y": 1.0})
        self.assertFalse(report["proof_discharged"])
        self.assertAlmostEqual(api["szl_weighted_geomean"]([0, 0.81], [0, 1]), 0.81, places=12)
        self.assertEqual(api["szl_weighted_geomean"]([0, 0.81], [0.5, 0.5]), 0)
        with self.assertRaises(ValueError):
            api["szl_weighted_geomean"]([1.1, 0.5])
        self.record("math-claim-check", {"status": report["status"], "tested_cases": report["case_count"],
            "failed_inputs": report["failures"][0]["inputs"],
            "proof_discharged": report["proof_discharged"],
            "api_usability": "Initial SKILL omitted normalized weights; current docs clarify the sum-to-one precondition",
            "proof_obligation": "Exact domain-wide statement remains separate from binary64 observations"})

    def test_math_scope_uses_synthetic_proof_records_without_certification(self):
        api = module("math-claim-check")
        statement = "For real x in [0,1], the square x*x is nonnegative."
        proposition = "Synthetic supplied proposition: forall x in [0,1], x*x >= 0"
        formal_source, runtime_source = digest("invented formal source v1"), digest("invented runtime source v1")
        toolchain, dependency_lock = digest("invented toolchain bytes"), digest("invented dependency lock")
        domain = {"x": {"lower": 0, "upper": 1, "lower_inclusive": True, "upper_inclusive": True}}
        log = "SYNTHETIC hypothetical checker report; no Lean invocation occurred.\n"
        scope = {"schema": "szl.math-claim-scope.v1",
                 "claim": {"statement": statement, "relation": "ge"},
                 "theorem": {"symbol": "Toy.square_nonnegative", "proposition": proposition,
                    "relation": "ge", "assumptions": ["x_real"], "domain": copy.deepcopy(domain),
                    "source_sha256": formal_source, "toolchain_sha256": toolchain,
                    "dependency_lock_sha256": dependency_lock},
                 "runtime": {"relation": "ge", "assumptions": ["x_real"], "domain": copy.deepcopy(domain),
                             "source_sha256": runtime_source, "numeric_semantics": "binary64"},
                 "observed_artifacts": {"formal_source": formal_source, "runtime_source": runtime_source,
                                        "toolchain": toolchain, "dependency_lock": dependency_lock},
                 "allowed_axioms": [],
                 "proof_record": {"theorem_symbol": "Toy.square_nonnegative",
                    "proposition_sha256": digest(proposition), "source_sha256": formal_source,
                    "toolchain_sha256": toolchain, "dependency_lock_sha256": dependency_lock,
                    "log": log, "log_sha256": digest(log), "exit_code": 0,
                    "contains_sorry": False, "contains_admit": False,
                    "transitive_axioms": [], "custom_axioms": []},
                 "correspondence": {"statement_sha256": digest(statement),
                    "proposition_sha256": digest(proposition), "runtime_source_sha256": runtime_source,
                    "reviewer": "Synthetic review declaration", "evidence_sha256": digest("invented correspondence"),
                    "informal_to_formal": "REVIEWED", "formal_to_runtime": "REVIEWED",
                    "numeric_semantics_reviewed": True}}
        clean = api["szl_audit_math_scope"](scope)
        self.assertEqual(clean["status"], "STRUCTURAL_CHECKS_PASSED")
        self.assertFalse(clean["proof_discharged"])
        self.assertFalse(clean["authentic"])
        extended = copy.deepcopy(scope)
        extended["runtime"]["domain"]["x"]["lower"] = -1
        blocked = api["szl_audit_math_scope"](extended)
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertIn("RUNTIME_OUTSIDE_THEOREM_DOMAIN", [f["code"] for f in blocked["findings"]])
        absent = copy.deepcopy(scope)
        absent.pop("proof_record")
        unknown = api["szl_audit_math_scope"](absent)
        self.assertEqual(unknown["status"], "UNKNOWN")
        self.assertIn("MISSING_PROOF_LOG", [f["code"] for f in unknown["findings"]])
        self.record("math-scope", {"complete_synthetic_record_status": clean["status"],
            "proof_discharged": clean["proof_discharged"], "authentic": clean["authentic"],
            "extended_domain_status": blocked["status"], "missing_log_status": unknown["status"],
            "formal_checker_execution": "NOT_RUN"})

    def test_research_anatomy_propagates_changed_source(self):
        api = module("research-anatomy")
        graph = {"schema": "szl.research-anatomy.v1", "nodes": [
            {"id": "data", "kind": "dataset", "title": "Invented sensor table", "sha256": digest("rows v1")},
            {"id": "run", "kind": "run", "title": "Saved synthetic scoring", "depends_on": ["data"],
             "sha256": digest("saved output")},
            {"id": "claim", "kind": "claim", "title": "Candidate improved", "depends_on": ["run"],
             "claim_state": "MEASURED"}]}
        observed = {"data": digest("rows v2"), "run": digest("saved output")}
        report = api["szl_anatomy_assess"](graph, observed)
        self.assertEqual(report["changed_sources"], ["data"])
        self.assertEqual(report["recheck"], ["claim", "data", "run"])
        nodes = {row["id"]: row for row in report["nodes"]}
        self.assertEqual(nodes["run"]["artifact_binding"], "MATCH")
        self.assertEqual(nodes["claim"]["state"], "STALE")
        self.assertFalse(nodes["claim"]["truth_verified"])
        replacement = dict(graph["nodes"][0], sha256=observed["data"])
        updated = api["szl_anatomy_update"](graph, [replacement])
        self.assertEqual(graph["nodes"][0]["sha256"], digest("rows v1"))
        self.assertEqual(updated["history"][0]["previous"], graph["nodes"][0])
        self.assertFalse(updated["history"][0]["signed"])
        self.assertTrue(next(n for n in updated["nodes"] if n["id"] == "claim")["needs_recheck"])
        self.record("research-anatomy", {"changed_sources": report["changed_sources"],
            "recheck": report["recheck"], "claim_truth_verified": nodes["claim"]["truth_verified"],
            "correction_history_signed": updated["history"][0]["signed"]})

    def test_research_ledger_preserves_conflict_expiry_and_unchecked_clock(self):
        api = module("research-anatomy")
        graph = {"schema": "szl.research-anatomy.v1", "nodes": [
            {"id": "support", "kind": "run", "title": "Invented positive saved run",
             "sha256": digest("positive synthetic run"), "observed_at": "2026-09-30T09:00:00Z",
             "expires_at": "2026-09-30T13:00:00Z"},
            {"id": "counter", "kind": "run", "title": "Invented negative saved run",
             "sha256": digest("negative synthetic run"), "observed_at": "2026-09-30T10:00:00Z"},
            {"id": "claim", "kind": "claim", "title": "Candidate improves sensor forecasts"},
            {"id": "decision", "kind": "decision", "title": "Next experiment", "depends_on": ["claim"]},
            {"id": "unrelated", "kind": "question", "title": "Separate unit calibration question"}],
            "evidence_links": [{"claim": "claim", "evidence": "support", "relation": "supports"},
                               {"claim": "claim", "evidence": "counter", "relation": "contradicts"}]}
        readbacks = {"support": digest("positive synthetic run"), "counter": digest("negative synthetic run")}
        conflict = api["szl_anatomy_assess"](graph, readbacks, as_of="2026-09-30T12:00:00Z")
        self.assertEqual(conflict["conflicting_claims"], ["claim"])
        self.assertEqual(conflict["recheck"], ["claim", "decision"])
        self.assertEqual(next(n for n in conflict["nodes"] if n["id"] == "claim")["evidence_state"], "CONFLICTING")
        expired = api["szl_anatomy_assess"](graph, readbacks, as_of="2026-09-30T13:00:00Z")
        self.assertEqual(expired["expired_sources"], ["support"])
        self.assertEqual(expired["conflicting_claims"], [])
        self.assertEqual(expired["contradicted_claims"], ["claim"])
        self.assertNotIn("unrelated", expired["recheck"])
        unchecked = api["szl_anatomy_assess"](graph, readbacks)
        self.assertEqual(unchecked["expiry_not_checked"], ["support"])
        self.record("research-evidence-ledger", {"conflicting_claims": conflict["conflicting_claims"],
            "conflict_recheck": conflict["recheck"], "expiry_boundary_sources": expired["expired_sources"],
            "active_contradicted_claims": expired["contradicted_claims"],
            "omitted_clock_expiry_not_checked": unchecked["expiry_not_checked"],
            "scientific_truth": "NOT_MEASURED"})

    def test_artifact_lineage_compares_two_stage_versions(self):
        api = module("artifact-lineage")
        raw, features, output = digest("invented raw table"), digest("invented features"), digest("saved metrics")
        def stage(name, parent, result, parent_hash, output_hash):
            return {"id": name, "operation": "Declared synthetic " + name,
                    "code_sha256": digest(name + " source"), "config_sha256": digest(name + " config"),
                    "required_inputs": [parent], "inputs": [{"artifact": parent, "sha256": parent_hash}],
                    "outputs": [{"artifact": result, "sha256": output_hash}]}
        manifest = {"schema": "szl.artifact-lineage.v1", "artifacts": [
            {"id": "raw-v1", "kind": "source", "sha256": raw},
            {"id": "features-v1", "kind": "derived", "sha256": features},
            {"id": "metrics-v1", "kind": "derived", "sha256": output}],
            "stages": [stage("prepare", "raw-v1", "features-v1", raw, features),
                       stage("score", "features-v1", "metrics-v1", features, output)],
            "required_stages": ["prepare", "score"],
            "observed_digests": {"raw-v1": raw, "features-v1": features, "metrics-v1": output}}
        report = api["szl_audit_lineage"](manifest)
        self.assertEqual(report["status"], "CONTINUITY_ON_SUPPLIED_DIGESTS")
        self.assertEqual(report["stage_order"], ["prepare", "score"])
        self.assertFalse(report["authentic"])
        self.assertFalse(report["transformations_executed"])
        self.assertFalse(report["code_and_config_pins_verified"])
        changed = copy.deepcopy(manifest)
        changed["stages"][1]["inputs"][0]["sha256"] = digest("features-v2 substituted")
        mismatch = api["szl_audit_lineage"](changed)
        self.assertEqual(mismatch["status"], "REVIEW_REQUIRED")
        self.assertIn("PARENT_DIGEST_MISMATCH", [f["code"] for f in mismatch["findings"]])
        absent = copy.deepcopy(manifest)
        absent["observed_digests"].pop("features-v1")
        missing = api["szl_audit_lineage"](absent)
        self.assertEqual(missing["status"], "REVIEW_REQUIRED")
        self.record("artifact-lineage", {"status": report["status"],
            "substituted_parent_findings": [f["code"] for f in mismatch["findings"]],
            "missing_readback_findings": [f["code"] for f in missing["findings"]],
            "transformations_executed": report["transformations_executed"]})

    def test_unit_invariants_convert_before_numeric_check(self):
        api = module("unit-invariants")
        def quantity(name, unit, dimensions, values):
            return {"id": name, "unit": unit, "dimension": dimensions, "values": values,
                    "range_si": {"min": "0", "max": "10"}}
        record = {"schema": "szl.unit-invariants.v1", "quantities": [
            quantity("distance", "mm", [1, 0, 0, 0, 0, 0, 0], ["1000", "2000"]),
            quantity("elapsed", "s", [0, 0, 1, 0, 0, 0, 0], ["2", "4"]),
            quantity("velocity", "m/s", [1, 0, -1, 0, 0, 0, 0], ["0.5", "0.5"])],
            "invariants": [{"id": "velocity-check", "operation": "ratio",
                            "operands": ["distance", "elapsed"], "result": "velocity",
                            "atol_si": "0", "rtol": "0"}]}
        report = api["szl_audit_unit_invariants"](record)
        self.assertEqual(report["status"], "PASS_DECLARED_CHECKS")
        self.assertFalse(report["observed_units_verified"])
        self.assertFalse(report["physical_law_verified"])
        wrong = copy.deepcopy(record)
        wrong["quantities"][2]["values"] = ["500", "500"]
        mismatch = api["szl_audit_unit_invariants"](wrong)
        self.assertEqual(mismatch["status"], "REVIEW_REQUIRED")
        self.assertIn("SCALE_MISMATCH", [f["code"] for f in mismatch["findings"]])
        unsupported = copy.deepcopy(record)
        unsupported["quantities"][0]["unit"] = "degC"
        with self.assertRaises(ValueError):
            api["szl_audit_unit_invariants"](unsupported)
        self.record("unit-invariants", {"status": report["status"],
            "scale_error_findings": [f["code"] for f in mismatch["findings"]],
            "observed_units_verified": report["observed_units_verified"],
            "physical_law_verified": report["physical_law_verified"]})

    def test_negative_control_keeps_null_failure_and_missing_outcome(self):
        api = module("negative-control-audit")
        payload = control_fixture()
        report = api["szl_audit_negative_controls"](payload)
        self.assertEqual(report["status"], "CONTROLS_CONSISTENT_ON_SUPPLIED_EVIDENCE")
        self.assertEqual(report["preregistration"], "DECLARED_ONLY")
        self.assertEqual(report["scientific_performance"], "NOT_MEASURED")
        changed = copy.deepcopy(payload)
        artifact = changed["artifacts"][-1]
        outcome = json.loads(artifact["content_utf8"])
        outcome["delta"] = 0.02
        artifact["content_utf8"] = canonical(outcome).decode("utf-8")
        artifact["sha256"] = digest(artifact["content_utf8"])
        failed_null = api["szl_audit_negative_controls"](changed)
        self.assertEqual(failed_null["status"], "INCONCLUSIVE")
        missing_payload = copy.deepcopy(payload)
        missing_payload["results"] = []
        missing = api["szl_audit_negative_controls"](missing_payload)
        self.assertEqual(missing["status"], "INCONCLUSIVE")
        readout_only_nuisance = copy.deepcopy(payload)
        readout_only_nuisance["registry"]["graph"]["edges"].remove(["batch", "algorithm"])
        readout_artifact = readout_only_nuisance["artifacts"][-1]
        readout_outcome = json.loads(readout_artifact["content_utf8"])
        readout_outcome["registry_sha256"] = object_digest(readout_only_nuisance["registry"])
        readout_artifact["content_utf8"] = canonical(readout_outcome).decode("utf-8")
        readout_artifact["sha256"] = digest(readout_artifact["content_utf8"])
        narrow_scope = api["szl_audit_negative_controls"](readout_only_nuisance)
        self.assertEqual(narrow_scope["status"], "INCONCLUSIVE")
        self.assertIn("NUISANCE_NOT_SHARED_IN_DECLARED_GRAPH", [f["code"] for f in narrow_scope["findings"]])
        self.record("negative-control-audit", {"status": report["status"],
            "null_failure_status": failed_null["status"],
            "null_failure_findings": [f["code"] for f in failed_null["findings"]],
            "missing_outcome_findings": [f["code"] for f in missing["findings"]],
            "readout_only_nuisance_status": narrow_scope["status"],
            "contract_limit": "Shared nuisance must affect intervention and readout; readout-only nuisance is outside scope",
            "mechanism_validity": "Declared graph only; power and nuisance coverage unmeasured"})

    def test_analysis_plan_retains_failed_attempt_and_exploratory_amendment(self):
        api = module("analysis-plan-audit")
        supplied = plan_fixture()
        report = api["szl_audit_analysis_plan"](supplied)
        self.assertEqual(report["status"], "CONSISTENT_WITH_DECLARED_PLAN")
        self.assertEqual(report["attempt_counts"], {"SUCCESS": 1, "FAILED": 1, "ABORTED": 0})
        self.assertEqual(report["preregistration"], "DECLARED_ONLY")
        self.assertEqual(report["statistical_inference"], "NOT_PERFORMED")
        amended = copy.deepcopy(supplied)
        amended["run"]["hypotheses"][0]["practical_margin"] = 0.05
        amended["deviations"] = [{"id": "margin-change", "plan_version": 1,
            "path": "/hypotheses/forecast-error/practical_margin",
            "planned_sha256": object_digest(0.1), "observed_sha256": object_digest(0.05),
            "declared_at": "2026-09-30T09:05:00Z", "reason": "Synthetic post-output amendment"}]
        changed = api["szl_audit_analysis_plan"](amended)
        self.assertEqual(changed["status"], "EXPLORATORY")
        self.assertIn("LOGGED_EXPLORATORY_DEVIATION", [f["code"] for f in changed["findings"]])
        self.assertIn("DEVIATION_AFTER_RUN_START", [f["code"] for f in changed["findings"]])
        incomplete = copy.deepcopy(supplied)
        incomplete["run"]["attempts"].pop()
        missing = api["szl_audit_analysis_plan"](incomplete)
        self.assertEqual(missing["status"], "EXPLORATORY")
        self.assertIn("MISSING_SCHEDULED_ATTEMPT", [f["code"] for f in missing["findings"]])
        self.record("analysis-plan-audit", {"status": report["status"],
            "attempt_counts": report["attempt_counts"], "amended_status": changed["status"],
            "amendment_findings": [f["code"] for f in changed["findings"]],
            "incomplete_attempt_status": missing["status"],
            "statistical_inference": report["statistical_inference"]})


if __name__ == "__main__":
    unittest.main()
