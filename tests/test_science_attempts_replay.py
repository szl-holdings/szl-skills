"""Original bounded acceptance cases: synthetic JSON/temporary files, no replay."""
# SPDX-License-Identifier: Apache-2.0
import copy
import hashlib
import json
import pathlib
import runpy
import stat
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_kernel(name):
    return runpy.run_path(str(ROOT / "skills" / name / "kernel.py"))


class AttemptLedgerTests(unittest.TestCase):
    def setUp(self):
        self.kernel = load_kernel("szl-model-evaluation")
        self.audit = self.kernel["szl_audit_attempts"]
        self.payload = json.loads((ROOT / "skills" / "szl-model-evaluation" / "assets" / "attempts-example.json").read_text(encoding="utf-8"))

    def test_timeout_keeps_planned_and_quality_denominators(self):
        report = self.audit(**self.payload)
        self.assertEqual((report["planned"], report["recorded"], report["attempted"], report["completed"], report["successful"]), (3, 3, 3, 2, 2))
        self.assertEqual(report["status_counts"]["timeout"], 1)
        self.assertEqual(report["planned_accuracy"]["denominator"], 3)
        self.assertAlmostEqual(report["planned_accuracy"]["value"], 2 / 3)
        self.assertEqual(report["conditional_probability_metrics"]["accuracy"], 1)
        self.assertEqual(report["probability_metric_coverage"], {"numerator": 2, "denominator": 3})
        self.assertEqual(report["scientific_performance"], "NOT_MEASURED")
        self.assertEqual(report["prediction_model_binding"], "NOT_VERIFIED")

    def test_absent_attempt_is_missing_not_success(self):
        self.payload["attempts"].pop()
        report = self.audit(**self.payload)
        self.assertEqual(report["status"], "INCOMPLETE_ATTEMPT_LEDGER")
        self.assertEqual(report["status_counts"]["missing"], 1)
        self.assertEqual(report["planned_accuracy"]["denominator"], 3)
        self.assertEqual(report["ledger"][-1]["status"], "missing")

    def test_invalid_output_is_completed_and_unsuccessful(self):
        self.payload["attempts"][-1]["status"] = "invalid_output"
        report = self.audit(**self.payload)
        self.assertEqual((report["attempted"], report["completed"], report["successful"]), (3, 3, 2))
        self.assertEqual(report["planned_accuracy"]["numerator"], 2)

    def test_unavailable_is_recorded_but_not_attempted(self):
        self.payload["attempts"][-1]["status"] = "unavailable"
        report = self.audit(**self.payload)
        self.assertEqual((report["recorded"], report["attempted"], report["completed"]), (3, 2, 2))
        self.assertEqual(report["status_counts"]["missing"], 0)

    def test_all_failure_statuses_and_empty_observations_never_manufacture_metrics(self):
        for status in ("timeout", "failed", "aborted", "invalid_output", "unavailable"):
            observations = [{"attempt_id": plan["attempt_id"], "row_id": plan["row_id"], "status": status, "reason": "Synthetic failure"} for plan in self.payload["planned_attempts"]]
            report = self.audit(self.payload["planned_attempts"], observations)
            self.assertIsNone(report["conditional_probability_metrics"])
            self.assertEqual(report["planned_accuracy"]["value"], 0)
            self.assertEqual(report["status_counts"][status], 3)
        report = self.audit(self.payload["planned_attempts"], [])
        self.assertEqual(report["status_counts"]["missing"], 3)
        self.assertEqual(report["attempted"], 0)

    def test_complete_success_preserves_actual_binary_scoring(self):
        last = self.payload["attempts"][-1]
        last.update(status="success", probability=0.8)
        del last["reason"]
        report = self.audit(**self.payload)
        direct = self.kernel["szl_binary_metrics"]([0.1, 0.9, 0.8], [0, 1, 1])
        self.assertEqual(report["conditional_probability_metrics"], direct)
        self.assertEqual(report["planned_accuracy"]["value"], 1)
        self.assertEqual(report["issues"], [])

    def test_plan_digest_detects_label_change_and_is_order_independent(self):
        first = self.audit(**self.payload)
        self.payload["planned_attempts"].reverse()
        self.payload["attempts"].reverse()
        second = self.audit(**self.payload, expected_plan_sha256=first["plan_sha256"])
        self.assertEqual(first["input_sha256"], second["input_sha256"])
        self.assertEqual(second["plan_binding"], "MATCHED_RETAINED_DIGEST")
        self.payload["planned_attempts"][0]["label"] = 0
        with self.assertRaises(ValueError):
            self.audit(**self.payload, expected_plan_sha256=first["plan_sha256"])

    def test_duplicate_unplanned_wrong_row_and_saved_correctness_rejected(self):
        for mode in ("duplicate", "unplanned", "row", "correct", "label"):
            payload = copy.deepcopy(self.payload)
            if mode == "duplicate":
                payload["attempts"].append(copy.deepcopy(payload["attempts"][0]))
            elif mode == "unplanned":
                payload["attempts"][0]["attempt_id"] = "unknown"
            elif mode == "row":
                payload["attempts"][0]["row_id"] = "synthetic-row-2"
            elif mode == "correct":
                payload["attempts"][0]["correct"] = True
            else:
                payload["attempts"][0]["label"] = 1
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.audit(**payload)

    def test_nonfinite_boolean_out_of_range_and_non_success_probability_rejected(self):
        for probability in (float("nan"), float("inf"), True, -0.1, 1.1, None):
            payload = copy.deepcopy(self.payload)
            payload["attempts"][0]["probability"] = probability
            with self.subTest(probability=probability), self.assertRaises(ValueError):
                self.audit(**payload)
        self.payload["attempts"][-1]["probability"] = 0.5
        with self.assertRaises(ValueError):
            self.audit(**self.payload)

    def test_invalid_plan_labels_ids_and_bounds_rejected(self):
        for mode in ("bool", "duplicate", "conflicting_row", "empty_id", "oversize"):
            plan = copy.deepcopy(self.payload["planned_attempts"])
            if mode == "bool":
                plan[0]["label"] = True
            elif mode == "duplicate":
                plan.append(copy.deepcopy(plan[0]))
            elif mode == "conflicting_row":
                plan[1]["row_id"] = plan[0]["row_id"]
            elif mode == "empty_id":
                plan[0]["attempt_id"] = ""
            else:
                plan = [plan[0]] * 10001
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.audit(plan, [])


class ReplayManifestTests(unittest.TestCase):
    def setUp(self):
        self.kernel = load_kernel("szl-reproducibility-capsule")
        self.tmp = tempfile.TemporaryDirectory(prefix="szl-replay-synthetic-")
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.contents = {"input.txt": b"SYNTHETIC input\n", "analysis.py": b"raise RuntimeError('inert source was executed')\n",
                         "environment.json": b'{"python":"3.10+","dependencies":"stdlib"}\n',
                         "plan.json": b'{"seed":7,"comparison":"exact_bytes"}\n', "result.txt": b"SYNTHETIC reference output\n"}
        for name, content in self.contents.items():
            (self.root / name).write_bytes(content)
        self.files = [{"path": path, "role": role} for path, role in (("input.txt", "input"), ("analysis.py", "source"),
                      ("environment.json", "environment"), ("plan.json", "analysis_plan"), ("result.txt", "output"))]
        self.replay = {"schema": "szl.offline-replay.v1", "argv": ["python", "analysis.py", "--seed", "7"],
                       "environment": {"path": "environment.json", "sha256": self.digest("environment.json")},
                       "analysis_plan": {"path": "plan.json", "sha256": self.digest("plan.json")}, "seed": 7,
                       "limits": {"network": "denied", "process_spawn": "denied", "secret_access": "denied", "max_seconds": 30, "max_memory_mib": 128},
                       "expected_outputs": [{"path": "result.txt", "sha256": self.digest("result.txt"), "comparison": {"mode": "exact_bytes"}}]}

    def digest(self, name):
        return hashlib.sha256(self.contents[name]).hexdigest()

    def make(self, replay=None, files=None, metadata=None):
        return self.kernel["szl_make_capsule"](self.root, self.files if files is None else files,
                                              {"evidence_class": "SYNTHETIC"} if metadata is None else metadata,
                                              self.replay if replay is None else replay)

    def test_complete_retained_manifest_is_ready_but_never_executes_source(self):
        capsule = self.make()
        report = self.kernel["szl_verify_capsule"](self.root, capsule)
        self.assertEqual(report["integrity"], "MATCH")
        self.assertTrue(report["replay_ready"])
        self.assertFalse(report["authentic"])
        self.assertFalse(report["scientific_claims_verified"])
        self.assertEqual(report["execution"], "NOT_RUN")
        self.assertEqual(report["replay_validation"]["capability_denial"], "DECLARED_ONLY")
        self.assertEqual(report["replay_validation"]["tolerance_application"], "NOT_RUN")
        self.assertEqual(report["scientific_performance"], "NOT_MEASURED")
        self.assertEqual(sorted(self.contents), [item["path"] for item in report["files"]])

    def test_legacy_strings_and_no_replay_are_supported(self):
        capsule = self.kernel["szl_make_capsule"](self.root, ["input.txt"], {"seed": 7})
        report = self.kernel["szl_verify_capsule"](self.root, capsule)
        self.assertEqual(report["integrity"], "MATCH")
        self.assertFalse(report["replay_ready"])
        self.assertEqual(report["replay_validation"]["specification"], "NOT_SPECIFIED")

    def test_bundled_replay_fixture_binds_actual_synthetic_bytes(self):
        skill = ROOT / "skills" / "szl-reproducibility-capsule"
        payload = json.loads((skill / "assets" / "replay-example.json").read_text(encoding="utf-8"))
        capsule = self.kernel["szl_make_capsule"](skill, **payload)
        report = self.kernel["szl_verify_capsule"](skill, capsule)
        self.assertTrue(report["replay_ready"])
        self.assertEqual(report["execution"], "NOT_RUN")

    def test_changed_missing_and_tampered_prevent_readiness(self):
        capsule = self.make()
        (self.root / "input.txt").write_bytes(b"SYNTHETIC changed\n")
        report = self.kernel["szl_verify_capsule"](self.root, capsule)
        self.assertFalse(report["replay_ready"])
        self.assertEqual(next(item for item in report["files"] if item["path"] == "input.txt")["status"], "CHANGED")
        (self.root / "input.txt").unlink()
        report = self.kernel["szl_verify_capsule"](self.root, capsule)
        self.assertEqual(next(item for item in report["files"] if item["path"] == "input.txt")["status"], "MISSING")
        capsule["replay"]["seed"] = 9
        report = self.kernel["szl_verify_capsule"](self.root, capsule)
        self.assertEqual(report["integrity"], "MANIFEST_CHANGED")
        self.assertFalse(report["replay_ready"])

    def test_environment_plan_output_digests_and_missing_roles_rejected(self):
        for field in ("environment", "analysis_plan", "expected_outputs"):
            replay = copy.deepcopy(self.replay)
            reference = replay[field][0] if field == "expected_outputs" else replay[field]
            reference["sha256"] = "0" * 64
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.make(replay=replay)
        for role in ("input", "source", "environment", "analysis_plan", "output"):
            with self.subTest(role=role), self.assertRaises(ValueError):
                self.make(files=[entry for entry in self.files if entry["role"] != role])

    def test_duplicate_paths_outputs_and_invalid_roles_rejected(self):
        with self.assertRaises(ValueError):
            self.make(files=self.files + [self.files[0]])
        replay = copy.deepcopy(self.replay)
        replay["expected_outputs"].append(copy.deepcopy(replay["expected_outputs"][0]))
        with self.assertRaises(ValueError):
            self.make(replay=replay)
        files = copy.deepcopy(self.files)
        files[0]["role"] = "arbitrary"
        with self.assertRaises(ValueError):
            self.make(files=files)

    def test_traversal_absolute_private_configuration_and_secret_fields_rejected(self):
        for path in ("../escape", "/absolute", "C:/absolute", "a\\b", ".env", ".ssh/id_rsa", "secrets.json", "credentials.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.kernel["szl_make_capsule"](self.root, [path])
        for metadata in ({"api_key": "synthetic"}, {"nested": {"password": "synthetic"}}, {"secrets": "synthetic"}, {"access_token": "synthetic"}):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                self.make(metadata=metadata)

    def test_typed_denial_field_is_valid_but_injected_replay_credentials_are_not(self):
        capsule = self.make()
        self.assertEqual(capsule["replay"]["limits"]["secret_access"], "denied")
        self.assertTrue(self.kernel["szl_verify_capsule"](self.root, capsule)["replay_ready"])
        for field in ("api_key", "secrets", "password"):
            replay = copy.deepcopy(self.replay)
            replay[field] = "synthetic"
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.make(replay=replay)
        replay = copy.deepcopy(self.replay)
        replay["limits"]["password"] = "synthetic"
        with self.assertRaises(ValueError):
            self.make(replay=replay)

    def test_shell_credential_args_capabilities_seed_and_resource_bounds_rejected(self):
        variants = []
        for argv in (["sh", "analysis.py"], ["python", "-c", "print(1)"], ["python", "analysis.py", "--api-key", "synthetic"], ["python", "analysis.py", "x;exit"], ["python", "analysis.py", "line\nbreak"]):
            replay = copy.deepcopy(self.replay)
            replay["argv"] = argv
            variants.append(replay)
        for key, value in (("network", "allowed"), ("process_spawn", "allowed"), ("secret_access", "allowed"), ("max_seconds", 301), ("max_memory_mib", 1025)):
            replay = copy.deepcopy(self.replay)
            replay["limits"][key] = value
            variants.append(replay)
        replay = copy.deepcopy(self.replay)
        replay["seed"] = True
        variants.append(replay)
        for index, replay in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.make(replay=replay)

    def test_numeric_policy_is_validated_not_applied(self):
        replay = copy.deepcopy(self.replay)
        comparison = {"mode": "numeric_tolerance", "absolute_tolerance": 0.001, "relative_tolerance": 0.01, "predeclared": True}
        replay["expected_outputs"][0]["comparison"] = comparison
        report = self.kernel["szl_verify_capsule"](self.root, self.make(replay=replay))
        self.assertTrue(report["replay_ready"])
        self.assertEqual(report["replay_validation"]["tolerance_application"], "NOT_RUN")
        for key, value in (("absolute_tolerance", -1), ("relative_tolerance", float("nan")), ("relative_tolerance", True), ("predeclared", False)):
            broken = copy.deepcopy(replay)
            broken["expected_outputs"][0]["comparison"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.make(replay=broken)

    def test_symlink_and_reparse_roots_refused(self):
        link = self.root / "link.txt"
        try:
            link.symlink_to(self.root / "input.txt")
        except (OSError, NotImplementedError):
            pass
        else:
            with self.assertRaises(ValueError):
                self.kernel["szl_make_capsule"](self.root, ["link.txt"])
        fake = types.SimpleNamespace(st_file_attributes=0x400, st_mode=stat.S_IFDIR)
        original_lstat = pathlib.Path.lstat
        pathlib.Path.lstat = lambda path, *args, **kwargs: fake
        try:
            with self.assertRaises(ValueError):
                self.kernel["szl_make_capsule"](self.root, ["input.txt"])
        finally:
            pathlib.Path.lstat = original_lstat

    def test_file_count_size_and_metadata_bounds_rejected_before_sweeps(self):
        with self.assertRaises(ValueError):
            self.kernel["szl_make_capsule"](self.root, ["input.txt"] * 129)
        with self.assertRaises(ValueError):
            self.make(metadata={"notes": "x" * 65537})
        # Simulate only stat metadata; never allocate beyond the sandbox's file cap.
        large = self.root / "large.txt"
        large.write_bytes(b"SYNTHETIC")
        original_stat = pathlib.Path.stat
        def bounded_stat(path, *args, **kwargs):
            actual = original_stat(path, *args, **kwargs)
            if path == large:
                return types.SimpleNamespace(st_size=8 * 1024 * 1024 + 1, st_mode=actual.st_mode,
                                             st_ino=actual.st_ino, st_dev=actual.st_dev,
                                             st_mtime_ns=actual.st_mtime_ns)
            return actual
        pathlib.Path.stat = bounded_stat
        try:
            with self.assertRaisesRegex(ValueError, "8 MiB"):
                self.kernel["szl_make_capsule"](self.root, ["large.txt"])
        finally:
            pathlib.Path.stat = original_stat

    def test_manifest_is_deterministic_and_self_consistent_duplicates_are_refused(self):
        first = self.make()
        second = self.make(files=list(reversed(self.files)))
        self.assertEqual(first, second)
        bad = copy.deepcopy(first)
        bad["files"].append(copy.deepcopy(bad["files"][0]))
        body = {key: value for key, value in bad.items() if key != "capsule_sha256"}
        bad["capsule_sha256"] = self.kernel["szl_capsule_digest"](body)
        with self.assertRaises(ValueError):
            self.kernel["szl_verify_capsule"](self.root, bad)


if __name__ == "__main__":
    unittest.main()
