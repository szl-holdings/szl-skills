# SPDX-License-Identifier: Apache-2.0
"""Synthetic structural contract tests; no prover, network or subprocesses."""
import copy
import hashlib
import json
import pathlib
import runpy
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name):
    return runpy.run_path(str(ROOT / "skills" / name / "kernel.py"))


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def scope_fixture():
    statement, proposition, log = "Synthetic integer identity", "forall n : Int, n = n", "SYNTHETIC checker record; no checker was run"
    interval = {"lower": 0, "upper": 10, "lower_inclusive": True, "upper_inclusive": True}
    return {
        "schema": "szl.math-claim-scope.v1",
        "claim": {"statement": statement, "relation": "equal"},
        "theorem": {"symbol": "Synthetic.identity", "proposition": proposition, "relation": "equal",
                    "source_sha256": "a" * 64, "toolchain_sha256": "b" * 64, "dependency_lock_sha256": "c" * 64,
                    "assumptions": ["integer-domain"], "domain": {"n": copy.deepcopy(interval)}},
        "runtime": {"source_sha256": "d" * 64, "relation": "equal", "assumptions": ["integer-domain"],
                    "numeric_semantics": "integer", "domain": {"n": copy.deepcopy(interval)}},
        "observed_artifacts": {"formal_source": "a" * 64, "runtime_source": "d" * 64, "toolchain": "b" * 64, "dependency_lock": "c" * 64},
        "allowed_axioms": [],
        "proof_record": {"theorem_symbol": "Synthetic.identity", "proposition_sha256": digest(proposition),
                         "source_sha256": "a" * 64, "toolchain_sha256": "b" * 64, "dependency_lock_sha256": "c" * 64,
                         "log": log, "log_sha256": digest(log), "exit_code": 0,
                         "contains_sorry": False, "contains_admit": False, "transitive_axioms": [], "custom_axioms": []},
        "correspondence": {"statement_sha256": digest(statement), "proposition_sha256": digest(proposition),
                           "runtime_source_sha256": "d" * 64, "reviewer": "SYNTHETIC fixture author",
                           "evidence_sha256": "e" * 64, "informal_to_formal": "REVIEWED",
                           "formal_to_runtime": "REVIEWED", "numeric_semantics_reviewed": False}}


def ledger_fixture():
    return {"schema": "szl.research-anatomy.v1", "as_of": "2026-09-30T12:00:00Z",
            "nodes": [
                {"id": "paper-a", "kind": "paper", "title": "Synthetic supporting observation", "sha256": "a" * 64,
                 "source_revision": "SYNTHETIC-v1", "observed_at": "2026-09-01T00:00:00Z", "expires_at": "2026-10-01T00:00:00Z"},
                {"id": "paper-b", "kind": "paper", "title": "Synthetic qualifying observation"},
                {"id": "claim", "kind": "claim", "title": "Synthetic conditional conclusion"},
                {"id": "decision", "kind": "decision", "title": "Synthetic follow-up", "depends_on": ["claim"]},
                {"id": "independent", "kind": "claim", "title": "Unrelated synthetic claim", "depends_on": ["paper-b"]}],
            "evidence_links": [{"claim": "claim", "evidence": "paper-a", "relation": "supports"},
                               {"claim": "claim", "evidence": "paper-b", "relation": "qualifies"}]}


class MathScopeTests(unittest.TestCase):
    def setUp(self):
        self.audit = load("szl-math-claim-check")["szl_audit_math_scope"]
        self.fixture = scope_fixture()

    def codes(self, report):
        return {item["code"] for item in report["findings"]}

    def test_complete_binding_passes_structure_without_certifying_proof(self):
        before = copy.deepcopy(self.fixture)
        report = self.audit(self.fixture)
        self.assertEqual(report["status"], "STRUCTURAL_CHECKS_PASSED")
        self.assertFalse(report["proof_discharged"])
        self.assertFalse(report["authentic"])
        self.assertEqual(report["scientific_evaluation"], "NOT_MEASURED")
        self.assertEqual(self.fixture, before)

    def test_wrong_theorem_and_changed_proposition_are_blocked(self):
        for key, value in (("theorem_symbol", "Synthetic.other"), ("proposition_sha256", "f" * 64)):
            with self.subTest(key=key):
                fixture = scope_fixture()
                fixture["proof_record"][key] = value
                report = self.audit(fixture)
                self.assertEqual(report["status"], "BLOCKED")
                self.assertIn("PROOF_RECORD_BINDING_MISMATCH", self.codes(report))

    def test_unfinished_proof_and_custom_or_unreviewed_axioms_block(self):
        for key, value in (("contains_sorry", True), ("contains_admit", True), ("custom_axioms", ["Unproved.bound"]), ("transitive_axioms", ["Unreviewed.choice"])):
            with self.subTest(key=key):
                fixture = scope_fixture()
                fixture["proof_record"][key] = value
                self.assertEqual(self.audit(fixture)["status"], "BLOCKED")

    def test_missing_log_is_unknown_and_forged_digest_is_blocked(self):
        del self.fixture["proof_record"]
        self.assertEqual(self.audit(self.fixture)["status"], "UNKNOWN")
        fixture = scope_fixture()
        fixture["proof_record"]["log"] += " changed"
        self.assertIn("PROOF_LOG_DIGEST_MISMATCH", self.codes(self.audit(fixture)))

    def test_failed_checker_and_pin_mismatch_are_retained(self):
        self.fixture["proof_record"]["exit_code"] = 2
        self.fixture["proof_record"]["dependency_lock_sha256"] = "f" * 64
        codes = self.codes(self.audit(self.fixture))
        self.assertIn("PROOF_TOOL_REPORTED_FAILURE", codes)
        self.assertIn("PROOF_RECORD_BINDING_MISMATCH", codes)

    def test_one_direction_cannot_establish_iff(self):
        self.fixture["claim"]["relation"] = "iff"
        self.fixture["theorem"]["relation"] = "implies"
        self.fixture["runtime"]["relation"] = "implies"
        self.assertIn("CLAIM_RELATION_NOT_ESTABLISHED", self.codes(self.audit(self.fixture)))

    def test_runtime_domain_extension_and_missing_assumption_block(self):
        self.fixture["runtime"]["domain"]["n"]["upper"] = 11
        self.fixture["runtime"]["assumptions"] = []
        codes = self.codes(self.audit(self.fixture))
        self.assertIn("RUNTIME_OUTSIDE_THEOREM_DOMAIN", codes)
        self.assertIn("MISSING_RUNTIME_ASSUMPTIONS", codes)

    def test_boundary_inclusion_and_narrowing(self):
        self.fixture["theorem"]["domain"]["n"]["lower_inclusive"] = False
        self.assertEqual(self.audit(self.fixture)["status"], "BLOCKED")
        self.fixture["runtime"]["domain"]["n"]["lower"] = 1
        self.assertEqual(self.audit(self.fixture)["status"], "STRUCTURAL_CHECKS_PASSED")

    def test_floating_point_correspondence_requires_recorded_review(self):
        self.fixture["runtime"]["numeric_semantics"] = "binary64"
        self.assertIn("FLOATING_POINT_BOUNDARY_NOT_REVIEWED", self.codes(self.audit(self.fixture)))
        self.fixture["correspondence"]["numeric_semantics_reviewed"] = True
        self.assertEqual(self.audit(self.fixture)["status"], "STRUCTURAL_CHECKS_PASSED")

    def test_missing_readback_and_changed_implementation_cannot_pass(self):
        self.fixture["observed_artifacts"].pop("runtime_source")
        self.assertEqual(self.audit(self.fixture)["status"], "UNKNOWN")
        self.fixture["observed_artifacts"]["runtime_source"] = "f" * 64
        self.assertEqual(self.audit(self.fixture)["status"], "BLOCKED")

    def test_changed_informal_statement_breaks_correspondence(self):
        self.fixture["claim"]["statement"] += " and all real inputs"
        self.assertIn("CORRESPONDENCE_BINDING_MISMATCH", self.codes(self.audit(self.fixture)))

    def test_types_nonfinite_duplicate_ids_and_schema_are_rejected(self):
        for mode in ("schema", "bool", "nan", "duplicate", "oversized", "flag"):
            with self.subTest(mode=mode):
                fixture = scope_fixture()
                if mode == "schema":
                    fixture["schema"] = "unknown"
                elif mode == "bool":
                    fixture["runtime"]["domain"]["n"]["upper"] = True
                elif mode == "nan":
                    fixture["runtime"]["domain"]["n"]["upper"] = float("nan")
                elif mode == "duplicate":
                    fixture["theorem"]["assumptions"] *= 2
                elif mode == "oversized":
                    fixture["proof_record"]["log"] = "x" * 65537
                else:
                    fixture["proof_record"]["contains_sorry"] = "false"
                with self.assertRaises(ValueError):
                    self.audit(fixture)

    def test_deterministic_report_ignores_mapping_and_assumption_order(self):
        fixture = scope_fixture()
        fixture["theorem"]["assumptions"] = ["integer-domain", "nonnegative"]
        self.fixture["theorem"]["assumptions"] = ["nonnegative", "integer-domain"]
        first, second = self.audit(fixture), self.audit(self.fixture)
        self.assertEqual(first["findings"], second["findings"])
        self.assertEqual(first["status"], second["status"])


class ResearchLedgerTests(unittest.TestCase):
    def setUp(self):
        self.functions = load("szl-research-anatomy")
        self.assess = self.functions["szl_anatomy_assess"]
        self.fixture = ledger_fixture()

    def test_current_support_and_qualification_preserve_boundary(self):
        before = copy.deepcopy(self.fixture)
        report = self.assess(self.fixture, {"paper-a": "a" * 64})
        self.assertEqual(report["recheck"], [])
        self.assertEqual(next(node for node in report["nodes"] if node["id"] == "claim")["evidence_state"], "SUPPORTED")
        self.assertFalse(any(node["truth_verified"] for node in report["nodes"]))
        self.assertEqual(self.fixture, before)

    def test_expiry_is_inclusive_and_descendant_only(self):
        self.fixture["as_of"] = "2026-09-30T23:59:59Z"
        report = self.assess(self.fixture, as_of=None)
        self.assertEqual(report["expired_sources"], [])
        self.fixture["as_of"] = "2026-10-01T00:00:00Z"
        report = self.assess(self.fixture)
        self.assertEqual(report["expired_sources"], ["paper-a"])
        self.assertEqual(report["recheck"], ["claim", "decision", "paper-a"])
        self.assertNotIn("independent", report["recheck"])

    def test_contradiction_without_recorded_support_still_requires_review(self):
        self.fixture["evidence_links"][0]["relation"] = "contradicts"
        report = self.assess(self.fixture)
        self.assertEqual(report["conflicting_claims"], [])
        self.assertEqual(report["contradicted_claims"], ["claim"])
        self.assertEqual(report["recheck"], ["claim", "decision"])
        self.assertEqual(next(node for node in report["nodes"] if node["id"] == "claim")["evidence_state"], "CONTRADICTED")

    def test_no_clock_never_reports_expiry_checked(self):
        del self.fixture["as_of"]
        report = self.assess(self.fixture)
        self.assertEqual(report["expiry_not_checked"], ["paper-a"])
        self.assertEqual(report["expired_sources"], [])
        self.assertEqual(next(node for node in report["nodes"] if node["id"] == "paper-a")["freshness"], "NOT_CHECKED")

    def test_conflicting_evidence_is_retained_and_propagated(self):
        self.fixture["evidence_links"].append({"claim": "claim", "evidence": "paper-b", "relation": "contradicts"})
        report = self.assess(self.fixture)
        self.assertEqual(report["conflicting_claims"], ["claim"])
        self.assertEqual(report["recheck"], ["claim", "decision"])
        self.assertEqual(len(report["evidence_links"]), 3)
        self.assertNotIn("independent", report["recheck"])

    def test_retraction_and_correction_do_not_disappear(self):
        for status in ("retracted", "corrected"):
            with self.subTest(status=status):
                fixture = ledger_fixture()
                fixture["nodes"][0]["evidence_status"] = status
                report = self.assess(fixture)
                self.assertEqual(report["withdrawn_sources"], ["paper-a"])
                self.assertEqual(report["recheck"], ["claim", "decision", "paper-a"])
                self.assertFalse(next(link for link in report["evidence_links"] if link["evidence"] == "paper-a")["usable"])

    def test_changed_source_uses_evidence_edges_as_dependencies(self):
        report = self.assess(self.fixture, {"paper-a": "f" * 64})
        self.assertEqual(report["changed_sources"], ["paper-a"])
        self.assertEqual(report["recheck"], ["claim", "decision", "paper-a"])

    def test_correction_preserves_unsigned_history_and_link_descendants(self):
        changed = copy.deepcopy(self.fixture["nodes"][0])
        changed["source_revision"] = "SYNTHETIC-v2"
        updated = self.functions["szl_anatomy_update"](self.fixture, [changed])
        self.assertEqual(updated["history"][0]["previous"]["source_revision"], "SYNTHETIC-v1")
        self.assertFalse(updated["history"][0]["signed"])
        self.assertEqual(self.assess(updated)["recheck"], ["claim", "decision"])
        self.assertNotIn("needs_recheck", self.fixture["nodes"][2])

    def test_cycle_via_evidence_and_missing_parent_rejected(self):
        for mode in ("cycle", "missing", "duplicate", "relation", "target"):
            with self.subTest(mode=mode):
                fixture = ledger_fixture()
                if mode == "cycle":
                    fixture["nodes"][0]["depends_on"] = ["decision"]
                elif mode == "missing":
                    fixture["evidence_links"][0]["evidence"] = "absent"
                elif mode == "duplicate":
                    fixture["evidence_links"].append(copy.deepcopy(fixture["evidence_links"][0]))
                elif mode == "relation":
                    fixture["evidence_links"][0]["relation"] = "proves"
                else:
                    fixture["evidence_links"][0]["claim"] = "paper-b"
                with self.assertRaises(ValueError):
                    self.assess(fixture)

    def test_invalid_time_timezone_equal_expiry_and_missing_observation_rejected(self):
        for mode in ("calendar", "timezone", "equal", "missing", "clock", "typedflag"):
            with self.subTest(mode=mode):
                fixture = ledger_fixture()
                if mode == "calendar":
                    fixture["nodes"][0]["observed_at"] = "2026-02-30T00:00:00Z"
                elif mode == "timezone":
                    fixture["as_of"] = "2026-09-30T12:00:00+00:00"
                elif mode == "equal":
                    fixture["nodes"][0]["expires_at"] = fixture["nodes"][0]["observed_at"]
                elif mode == "missing":
                    del fixture["nodes"][0]["observed_at"]
                elif mode == "clock":
                    fixture["as_of"] = True
                else:
                    fixture["nodes"][0]["needs_recheck"] = "false"
                with self.assertRaises(ValueError):
                    self.assess(fixture)

    def test_future_observation_and_clock_conflict(self):
        self.fixture["as_of"] = "2026-08-31T23:59:59Z"
        report = self.assess(self.fixture)
        self.assertEqual(report["future_observations"], ["paper-a"])
        self.assertEqual(report["recheck"], ["claim", "decision", "paper-a"])
        with self.assertRaises(ValueError):
            self.assess(self.fixture, as_of="2026-09-30T12:00:00Z")

    def test_output_order_is_stable_for_permuted_nodes_and_links(self):
        first = self.assess(self.fixture)
        self.fixture["nodes"].reverse()
        self.fixture["evidence_links"].reverse()
        second = self.assess(self.fixture)
        for key in ("nodes", "recheck", "evidence_links", "conflicting_claims"):
            self.assertEqual(first[key], second[key])

    def test_legacy_graph_and_functions_remain_supported(self):
        payload = json.loads((ROOT / "skills" / "szl-research-anatomy" / "assets" / "example.json").read_text())
        self.assertEqual(self.assess(**payload)["recheck"], ["conclusion", "data", "run"])
        functions = load("szl-math-claim-check")
        self.assertEqual(functions["szl_check_math_cases"]("synthetic", [{"lhs": 1, "rhs": 1}])["status"], "NO_COUNTEREXAMPLE_IN_TESTED_CASES")


if __name__ == "__main__":
    unittest.main()
