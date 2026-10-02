"""Synthetic package-to-result checks; no wheel is fetched or installed."""

import copy
import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PILOT = ROOT / "pilots/package-to-result"
spec = importlib.util.spec_from_file_location("package_to_result_pilot", PILOT / "kernel.py")
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)


def fixture():
    return kernel.loads_strict((PILOT / "assets/synthetic.json").read_bytes())


class PackageToResultPilotTests(unittest.TestCase):
    def assert_finding(self, record, status, code):
        result = kernel.check(record)
        self.assertEqual(result["status"], status, result)
        self.assertIn(code, result["findings"], result)
        self.assertFalse(result["attestation_cryptographically_verified"])
        self.assertFalse(result["actual_installation_observed"])
        self.assertFalse(result["analysis_execution_observed"])
        self.assertEqual(result["scientific_result_validity"], "NOT_EVALUATED")

    def test_synthetic_consistency_grants_no_external_authority(self):
        result = kernel.check(fixture())
        self.assertEqual(result["status"], "CONSISTENT_SUPPLIED_EVIDENCE", result)
        self.assertEqual(result["findings"], [])
        self.assertRegex(result["record_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(result["network_calls"], 0)
        self.assertFalse(result["candidate_code_executed"])
        self.assertFalse(result["attestation_cryptographically_verified"])
        self.assertFalse(result["actual_installation_observed"])
        self.assertFalse(result["analysis_execution_observed"])
        self.assertEqual(result["scientific_result_validity"], "NOT_EVALUATED")

    def test_operator_supplied_export_does_not_upgrade_authority(self):
        record = fixture()
        record["evidence_kind"] = "operator_supplied_export"
        result = kernel.check(record)
        self.assertEqual(result["status"], "CONSISTENT_SUPPLIED_EVIDENCE")
        self.assertFalse(result["attestation_cryptographically_verified"])
        self.assertFalse(result["actual_installation_observed"])

    def test_pypi_file_hash_mismatch(self):
        record = fixture()
        record["observed"]["pypi"]["sha256"] = "0" * 64
        self.assert_finding(record, "GAP_OR_CONFLICT", "PYPI_SHA_MISMATCH")

    def test_attestation_publisher_and_artifact_mismatch(self):
        record = fixture()
        record["observed"]["provenance"]["publisher_repository"] = "elsewhere/synthetic-math"
        record["observed"]["provenance"]["artifact_sha256"] = "0" * 64
        result = kernel.check(record)
        self.assertEqual(result["status"], "GAP_OR_CONFLICT")
        self.assertIn("PUBLISHER_REPOSITORY_MISMATCH", result["findings"])
        self.assertIn("PROVENANCE_ARTIFACT_MISMATCH", result["findings"])

    def test_source_digest_and_tag_mismatch(self):
        record = fixture()
        record["observed"]["provenance"]["source_digest"] = "0" * 40
        record["observed"]["github"]["resolved_commit"] = "1" * 40
        result = kernel.check(record)
        self.assertEqual(result["status"], "GAP_OR_CONFLICT")
        self.assertIn("PROVENANCE_SOURCE_MISMATCH", result["findings"])
        self.assertIn("GITHUB_COMMIT_MISMATCH", result["findings"])

    def test_tag_ref_mismatch(self):
        record = fixture()
        record["observed"]["github"]["tag_ref"] = "refs/tags/v0.1.1"
        self.assert_finding(record, "GAP_OR_CONFLICT", "GITHUB_REF_MISMATCH")

    def test_install_hash_mismatch(self):
        record = fixture()
        record["observed"]["install_report"]["install"][0]["download_info"]["archive_info"]["hashes"]["sha256"] = "0" * 64
        self.assert_finding(record, "GAP_OR_CONFLICT", "INSTALL_SHA_MISMATCH")

    def test_declared_dry_run_and_nonzero_install_do_not_pass(self):
        record = fixture()
        record["observed"]["install_invocation"]["dry_run"] = True
        record["observed"]["install_invocation"]["exit_code"] = 1
        result = kernel.check(record)
        self.assertEqual(result["status"], "GAP_OR_CONFLICT")
        self.assertIn("INSTALL_DRY_RUN_DECLARED", result["findings"])
        self.assertIn("INSTALL_EXIT_NONZERO_DECLARED", result["findings"])

    def test_yanked_distribution_does_not_pass(self):
        record = fixture()
        record["observed"]["install_report"]["install"][0]["is_yanked"] = True
        self.assert_finding(record, "GAP_OR_CONFLICT", "INSTALL_YANKED_DISTRIBUTION")

    def test_inspected_version_mismatch(self):
        record = fixture()
        record["observed"]["inspect_report"]["installed"][0]["metadata"]["version"] = "0.2.0"
        self.assert_finding(record, "GAP_OR_CONFLICT", "INSPECT_VERSION_MISMATCH")

    def test_duplicate_installed_identity_refused(self):
        record = fixture()
        report = record["observed"]["inspect_report"]["installed"]
        report.append(copy.deepcopy(report[0]))
        self.assert_finding(record, "REFUSED", "INSTALLED_TARGET_AMBIGUOUS")

    def test_unrelated_installed_distribution_is_not_target_ambiguity(self):
        record = fixture()
        report = record["observed"]["inspect_report"]
        report["installed"].append({"metadata": {"name": "unrelated", "version": "9.9"}})
        record["observed"]["session"]["environment_report_sha256"] = kernel._digest(report)
        self.assertEqual(kernel.check(record)["status"], "CONSISTENT_SUPPLIED_EVIDENCE")

    def test_missing_provenance_is_incomplete(self):
        record = fixture()
        del record["observed"]["provenance"]
        self.assert_finding(record, "INCOMPLETE", "PROVENANCE_MISSING")

    def test_absent_target_in_install_report_is_incomplete(self):
        record = fixture()
        record["observed"]["install_report"]["install"].clear()
        self.assert_finding(record, "INCOMPLETE", "INSTALL_TARGET_ABSENT")

    def test_environment_snapshot_change_invalidates_session(self):
        record = fixture()
        record["observed"]["inspect_report"]["environment"] = {"python_version": "9.9"}
        self.assert_finding(record, "GAP_OR_CONFLICT", "SESSION_ENVIRONMENT_MISMATCH")

    def test_import_and_interpreter_mismatch(self):
        record = fixture()
        record["observed"]["session"]["import_name"] = "another_module"
        record["observed"]["session"]["interpreter_id"] = "other-interpreter"
        result = kernel.check(record)
        self.assertEqual(result["status"], "GAP_OR_CONFLICT")
        self.assertIn("SESSION_IMPORT_MISMATCH", result["findings"])
        self.assertIn("SESSION_INTERPRETER_MISMATCH", result["findings"])

    def test_invalid_wheel_path_refused(self):
        record = fixture()
        record["expected"]["wheel_filename"] = "../synthetic_math-0.1.0-py3-none-any.whl"
        self.assert_finding(record, "REFUSED", "WHEEL_FILENAME_INVALID")

    def test_empty_or_unsafe_tag_ref_refused(self):
        for ref in ("refs/tags/", "refs/tags/a/../b", "refs/tags/a\n"):
            with self.subTest(ref=ref):
                record = fixture()
                record["expected"]["source_ref"] = ref
                self.assert_finding(record, "REFUSED", "SOURCE_REF_INVALID")

    def test_install_url_with_credentials_refused(self):
        record = fixture()
        record["observed"]["install_report"]["install"][0]["download_info"]["url"] = (
            "https://user:pass@example.invalid/synthetic_math-0.1.0-py3-none-any.whl"
        )
        self.assert_finding(record, "REFUSED", "INSTALL_ARTIFACT_URL_INVALID")

    def test_install_url_without_host_refused(self):
        record = fixture()
        record["observed"]["install_report"]["install"][0]["download_info"]["url"] = (
            "https:///synthetic_math-0.1.0-py3-none-any.whl"
        )
        self.assert_finding(record, "REFUSED", "INSTALL_ARTIFACT_URL_INVALID")

    def test_unknown_top_level_fields_refused(self):
        record = fixture()
        record["declared_pass"] = True
        self.assert_finding(record, "REFUSED", "RECORD_FIELDS_INVALID")

    def test_strict_loader_rejects_duplicate_and_nonfinite_json(self):
        with self.assertRaisesRegex(ValueError, "DUPLICATE_JSON_KEY"):
            kernel.loads_strict('{"schema":"a","schema":"b"}')
        with self.assertRaisesRegex(ValueError, "NON_FINITE_JSON_NUMBER"):
            kernel.loads_strict('{"value":NaN}')
        with self.assertRaisesRegex(ValueError, "JSON_TOO_LARGE"):
            kernel.loads_strict('{"padding":"' + 'x' * kernel.MAX_BYTES + '"}')

    def test_deep_python_object_refused_without_crashing(self):
        record = fixture()
        current = record
        for _ in range(1200):
            current["nested"] = {}
            current = current["nested"]
        self.assert_finding(record, "REFUSED", "RECORD_TOO_DEEP")


if __name__ == "__main__":
    unittest.main()
