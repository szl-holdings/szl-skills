"""szl-reviewer-pack keeps non-pass statuses of upgraded skills visible (recon N9).

Stdlib only, offline. The probe builds real reports with the sibling skills' own kernels
and checks that the pack lists each one as unresolved. One generated test per status name
added to the unresolved set; the committed sample project must render byte-identically to
the base commit (b8e259c).
"""
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
PACK_DIR = SKILLS / "szl-reviewer-pack"

ORIGINAL = ("FAIL", "DIVERGENT", "MISMATCH", "MISSING", "RECEIPT_TAMPERED", "ERROR", "BASELINE_FLAGGED")
ADDED = ("ABSTAIN", "FAIL_OPTIONAL_ONLY", "INCOMPARABLE", "NO_OUTCOMES_RECORDED", "RECORDED_WITH_MISSING_FILES",
         "BASELINE_NOT_TESTED", "EMPTY_RECEIPT", "EXTRA_FILES_PRESENT", "UNSTABLE", "PLAN_INCOMPLETE", "HOLD",
         "BLOCKED", "UNKNOWN", "PARITY_ISSUES", "EXTRA_MEMBERS", "INCOMPLETE", "REFUSED", "DIVERGED", "STALE")
# Pass-like top-level statuses of reports the pack may read (current kernels plus the fixed
# cross-lane vocabulary). On their own, without finding fields, they must stay resolved.
PASS_LIKE = ("PASS", "CONSISTENT", "MATCH", "RECORDED", "SCORED", "GENERATED", "MEASURED", "COMPLETE",
             "HARMONIZED", "NO_CHECKED_ISSUES", "STRUCTURAL_CHECKS_PASSED", "PASS_DECLARED_CHECKS",
             "STABLE", "ANALYZED", "PARITY_OK")
# (pack_sha256, sha256 of the rendered Markdown), computed with the base-commit kernel b8e259c.
SAMPLE_PINS = {
    "with_config": ("666820d647c61857c9b0f41eccfcb744e5ca047678493a50a06d8f6b6093f9ae",
                    "2b03d3ac6d5e229a813e7cc31cc1d850e7be65561b27d97935d4f9fb520a06b0"),
    "no_config": ("93a7976e4bfc895dd3f5c9cdca4fd2cf3cf4d45fcd661e5cc26046b86b907324",
                  "990fe0a5df33fb48230cd9063430b71d8e0336f8297b123a47b83fd5db6c67fe"),
}
NEGATIVE = ("mutation-scored.json", "cross-incomparable.json", "gate-abstain.json")
POSITIVE = ("cross-consistent.json",)


def load(name):
    spec = importlib.util.spec_from_file_location("rpst_" + name.replace("-", "_"), SKILLS / name / "kernel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def example(name, fname="example.json"):
    return json.loads((SKILLS / name / "assets" / fname).read_text(encoding="utf-8"))


K = load("szl-reviewer-pack")


def build(reports):
    """Pack over standalone reports only (no project.json, no runs)."""
    with tempfile.TemporaryDirectory(prefix="szl-rp-status-") as d:
        for name, report in reports.items():
            (pathlib.Path(d) / name).write_text(json.dumps(report), encoding="utf-8")
        return K.szl_build_pack(d, sorted(reports))


def real_reports():
    """SCORED 6/10 mutation, INCOMPARABLE cross-implementation, ABSTAIN evidence gate, CONSISTENT control."""
    mut, cross, gate = (load("szl-analysis-mutation-test"), load("szl-cross-implementation-check"),
                        load("szl-evidence-gate"))
    mutation = mut.szl_score_mutations(mut.szl_generate_mutations(example("szl-analysis-mutation-test")),
                                       example("szl-analysis-mutation-test", "outcomes-example.json"))
    doc = example("szl-cross-implementation-check")
    doc["b"]["results"] = dict(json.loads(json.dumps(doc["a"]["results"])), aic=512.3)
    incomparable = cross.szl_cross_implementation_check(doc)
    doc["b"]["results"].pop("aic")
    consistent = cross.szl_cross_implementation_check(doc)
    abstain = gate.szl_evidence_gate(SKILLS / "szl-evidence-gate" / "assets", example("szl-evidence-gate"))
    return {"mutation-scored.json": mutation, "cross-incomparable.json": incomparable,
            "gate-abstain.json": abstain, "cross-consistent.json": consistent}


def run_cli(*args):
    return subprocess.run([sys.executable, "-B", str(PACK_DIR / "scripts" / "run.py"), *args],
                          cwd=str(PACK_DIR), capture_output=True, text=True, timeout=60)


class ReviewerPackProbeTests(unittest.TestCase):
    def test_recon_probe_scored_incomparable_abstain_yield_unresolved(self):
        reports = real_reports()
        mutation, incomparable, abstain = (reports[n] for n in NEGATIVE)
        self.assertEqual((mutation["status"], mutation["coverage"], len(mutation["missed"])), ("SCORED", "6/10", 4))
        self.assertEqual((incomparable["status"], incomparable["incomparable"]), ("INCOMPARABLE", 1))
        self.assertEqual((abstain["status"], abstain["required_failures"]), ("ABSTAIN", []))
        pack = build({n: reports[n] for n in NEGATIVE})
        self.assertEqual(pack["status"], "REVIEW_REQUIRED")
        by_source = {u["source"]: u for u in pack["unresolved"]}
        self.assertEqual(set(by_source), set(NEGATIVE))
        self.assertIn({"field": "missed", "value": mutation["missed"], "count": 4},
                      by_source["mutation-scored.json"]["details"])
        self.assertEqual(by_source["cross-incomparable.json"]["status"], "INCOMPARABLE")
        self.assertIn({"field": "incomparable", "value": 1, "count": 1},
                      by_source["cross-incomparable.json"]["details"])
        self.assertEqual(by_source["gate-abstain.json"]["status"], "ABSTAIN")
        markdown = K.szl_render_markdown(pack)
        self.assertIn("- **gate-abstain.json** — ABSTAIN", markdown)
        self.assertIn("`missed` (4)", markdown)
        self.assertIn("`incomparable` (1)", markdown)
        self.assertNotIn("None among the checks that were run.", markdown)

    def test_consistent_control_and_full_mutation_coverage_stay_resolved(self):
        reports = real_reports()
        mut = load("szl-analysis-mutation-test")
        plan = mut.szl_generate_mutations(example("szl-analysis-mutation-test"))
        outcomes = example("szl-analysis-mutation-test", "outcomes-example.json")
        for variant in outcomes["variants"].values():
            variant["flagged"] = True
        full = mut.szl_score_mutations(plan, outcomes)
        self.assertEqual((full["status"], full["coverage"], full["missed"], full["not_tested"]), ("SCORED", "10/10", [], []))
        pack = build({"consistent.json": reports["cross-consistent.json"], "full.json": full})
        self.assertEqual(pack["status"], "NO_UNRESOLVED_CHECKED_FINDINGS")
        self.assertEqual(pack["unresolved"], [])

    def test_no_outcomes_recorded_surfaces_not_tested_list(self):
        mut = load("szl-analysis-mutation-test")
        report = mut.szl_score_mutations(mut.szl_generate_mutations(example("szl-analysis-mutation-test")),
                                         {"baseline_flagged": False, "variants": {}})
        self.assertEqual(report["status"], "NO_OUTCOMES_RECORDED")
        pack = build({"m.json": report})
        (entry,) = pack["unresolved"]
        self.assertEqual(entry["status"], "NO_OUTCOMES_RECORDED")
        self.assertEqual(entry["details"], [{"field": "not_tested", "value": report["not_tested"][:10],
                                             "count": len(report["not_tested"])}])

    def test_unbound_clustered_report_surfaces_inference_blockers(self):
        clu = load("szl-clustered-replication")
        doc = example("szl-clustered-replication")
        keep = {"donor-A-cell-0", "donor-B-cell-0", "donor-C-cell-0"}
        doc["plan"]["expected_cases"] = [c for c in doc["plan"]["expected_cases"] if c["id"] in keep]
        doc["plan"]["reported_n"] = 3
        doc["rows"] = [dict(r, baseline_loss=13, candidate_loss=10) for r in doc["rows"] if r["id"] in keep]
        unbound = clu.szl_clustered_replication(doc)
        bound = clu.szl_clustered_replication(doc, unbound["plan_sha256"])
        self.assertEqual((unbound["status"], unbound["findings"], unbound["inference_blockers"]),
                         ("DESCRIPTIVE_ONLY", [], ["EXPECTED_PLAN_COMMITMENT_UNAVAILABLE"]))
        self.assertEqual((bound["status"], bound["findings"], bound["inference_blockers"]),
                         ("CONDITIONAL_CLUSTER_RESULT", [], []))
        (entry,) = build({"unbound.json": unbound, "bound.json": bound})["unresolved"]
        self.assertEqual(entry, {"source": "unbound.json", "status": "DESCRIPTIVE_ONLY", "details": [
            {"field": "inference_blockers", "value": ["EXPECTED_PLAN_COMMITMENT_UNAVAILABLE"], "count": 1}]})
        self.assertEqual(build({"bound.json": bound})["status"], "NO_UNRESOLVED_CHECKED_FINDINGS")


class FindingFieldTests(unittest.TestCase):
    def test_new_fields_append_after_existing_ones(self):
        details = K._findings_of({"failures": ["a"], "missed": ["m"], "not_tested": ["t"], "inference_blockers": ["b"],
                                  "divergent": 2, "incomparable": 3})
        self.assertEqual([d["field"] for d in details],
                         ["failures", "missed", "not_tested", "inference_blockers", "divergent", "incomparable"])

    def test_empty_or_zero_new_fields_are_not_findings(self):
        self.assertEqual(K._findings_of({"missed": [], "not_tested": [], "incomparable": 0, "inference_blockers": []}), [])
        self.assertEqual(K._findings_of({"inference_blockers": None}), [])
        pack = build({"r.json": {"status": "SCORED", "missed": [], "not_tested": [], "incomparable": 0}})
        self.assertEqual(pack["status"], "NO_UNRESOLVED_CHECKED_FINDINGS")

    def test_field_level_inference_blockers_alone_make_a_pass_status_unresolved(self):
        # Field-level check only: no kernel emits ANALYZED with blockers but empty findings.
        blockers = ["EXPECTED_PLAN_COMMITMENT_UNAVAILABLE"]
        pack = build({"a.json": {"schema": "synthetic", "status": "ANALYZED", "plan_binding": "UNBOUND",
                                 "inference_blockers": blockers}})
        self.assertEqual(pack["status"], "REVIEW_REQUIRED")
        self.assertEqual(pack["unresolved"], [{"source": "a.json", "status": "ANALYZED", "details": [
            {"field": "inference_blockers", "value": blockers, "count": 1}]}])

    def test_ablation_shaped_analyzed_report_lists_findings_and_inference_blockers(self):
        # Shape of the clustered-ablation upgrade's ANALYZED output: every inference blocker is also
        # copied into findings with subject null, so the base kernel already listed it via findings.
        blockers = ["EXPECTED_PLAN_COMMITMENT_UNAVAILABLE"]
        findings = [{"finding": name, "subject": None} for name in blockers]
        report = {"schema": "szl.clustered-ablation.v1", "status": "ANALYZED", "inference_blockers": blockers,
                  "findings": findings}
        (entry,) = build({"ablation.json": report})["unresolved"]
        self.assertEqual(entry, {"source": "ablation.json", "status": "ANALYZED", "details": [
            {"field": "findings", "value": findings, "count": 1},
            {"field": "inference_blockers", "value": blockers, "count": 1}]})

    def test_integer_missing_count_is_not_a_finding_but_status_lists_it(self):
        sr = load("szl-session-receipt")
        receipt = sr.szl_make_session_receipt(SKILLS / "szl-session-receipt" / "assets" / "project",
                                              example("szl-session-receipt"))
        with tempfile.TemporaryDirectory(prefix="szl-rp-empty-") as empty:
            verification = sr.szl_verify_session_receipt(empty, receipt)
        self.assertEqual((verification["status"], verification["mismatches"], verification["missing"]), ("MISMATCH", 0, 3))
        self.assertEqual(K._findings_of(verification), [])
        (entry,) = build({"verify.json": verification})["unresolved"]
        self.assertEqual(entry, {"source": "verify.json", "status": "MISMATCH", "details": []})

    def test_readiness_hold_and_missing_status_are_unresolved(self):
        pack = build({"map.json": {"readiness": "HOLD"}, "bare.json": {"schema": "x"}})
        self.assertEqual({(u["source"], u["status"]) for u in pack["unresolved"]},
                         {("map.json", "HOLD"), ("bare.json", "UNKNOWN")})


class StatusSetTests(unittest.TestCase):
    def test_unresolved_set_is_original_plus_added(self):
        self.assertEqual(K.UNRESOLVED_STATUSES, frozenset(ORIGINAL) | frozenset(ADDED))
        self.assertEqual(len(ADDED), len(set(ADDED)))
        self.assertFalse(set(ORIGINAL) & set(ADDED))

    def test_pass_like_statuses_stay_resolved(self):
        self.assertFalse(K.UNRESOLVED_STATUSES & set(PASS_LIKE))
        for status in PASS_LIKE:
            with self.subTest(status=status):
                pack = build({"r.json": {"schema": "synthetic", "status": status}})
                self.assertEqual(pack["status"], "NO_UNRESOLVED_CHECKED_FINDINGS")

    def test_original_statuses_still_unresolved(self):
        for status in ORIGINAL:
            with self.subTest(status=status):
                (entry,) = build({"r.json": {"schema": "synthetic", "status": status}})["unresolved"]
                self.assertEqual(entry["status"], status)


def _added_status_test(status):
    def test(self):
        pack = build({"r.json": {"schema": "synthetic", "status": status}})
        self.assertEqual(pack["status"], "REVIEW_REQUIRED")
        self.assertEqual(pack["unresolved"], [{"source": "r.json", "status": status, "details": []}])
        self.assertIn("- **r.json** — " + status, K.szl_render_markdown(pack))
    test.__doc__ = "%s with no finding fields is listed as unresolved." % status
    return test


class AddedStatusTests(unittest.TestCase):
    pass


for _status in ADDED:
    setattr(AddedStatusTests, "test_added_status_%s" % _status.lower(), _added_status_test(_status))


class SampleAndFixtureTests(unittest.TestCase):
    def test_sample_project_renders_as_on_base_commit(self):
        config = json.loads((PACK_DIR / "assets" / "review-config.json").read_text(encoding="utf-8"))
        for label, cfg in (("with_config", config), ("no_config", None)):
            with self.subTest(label=label):
                pack = K.szl_build_pack(PACK_DIR / "assets" / "project", None, cfg)
                markdown = K.szl_render_markdown(pack)
                self.assertEqual((pack["pack_sha256"], hashlib.sha256(markdown.encode("utf-8")).hexdigest()),
                                 SAMPLE_PINS[label])

    def test_sample_project_cli_output_unchanged(self):
        with tempfile.TemporaryDirectory(prefix="szl-rp-cli-") as d:
            out_json, out_md = pathlib.Path(d) / "pack.json", pathlib.Path(d) / "REVIEW.md"
            p = run_cli("assets/project", "--config", "assets/review-config.json", "--json", str(out_json),
                        "--output", str(out_md))
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout)["unresolved"], 3)
            self.assertEqual(json.loads(out_json.read_text(encoding="utf-8"))["pack_sha256"], SAMPLE_PINS["with_config"][0])
            self.assertEqual(hashlib.sha256(out_md.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
                             SAMPLE_PINS["with_config"][1])

    def test_negative_fixture_cli_review_required(self):
        args = ["assets/status-probe"]
        for name in NEGATIVE:
            args += ["--extra", name]
        with tempfile.TemporaryDirectory(prefix="szl-rp-neg-") as d:
            p = run_cli(*args, "--output", str(pathlib.Path(d) / "REVIEW.md"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["status"], "REVIEW_REQUIRED")
        self.assertEqual(json.loads(p.stdout)["unresolved"], 3)

    def test_positive_fixture_cli_no_unresolved(self):
        with tempfile.TemporaryDirectory(prefix="szl-rp-pos-") as d:
            p = run_cli("assets/status-probe", "--extra", POSITIVE[0], "--output", str(pathlib.Path(d) / "REVIEW.md"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["status"], "NO_UNRESOLVED_CHECKED_FINDINGS")
        self.assertEqual(json.loads(p.stdout)["unresolved"], 0)

    def test_fixtures_match_fields_of_real_sibling_reports(self):
        reports = real_reports()
        for name in NEGATIVE + POSITIVE:
            with self.subTest(fixture=name):
                fixture = json.loads((PACK_DIR / "assets" / "status-probe" / name).read_text(encoding="utf-8"))
                self.assertEqual(fixture, {key: reports[name][key] for key in fixture})


if __name__ == "__main__":
    unittest.main()
