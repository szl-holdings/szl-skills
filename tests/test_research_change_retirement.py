# SPDX-License-Identifier: Apache-2.0
"""Empty retained snapshots must not erase the required-claim denominator."""
import copy
import json
import pathlib
import runpy
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/szl-research-change-impact"
PLAN = runpy.run_path(str(SKILL / "kernel.py"))["szl_plan_research_changes"]


def retirement():
    document = json.loads((SKILL / "assets/example.json").read_text(encoding="utf-8"))
    document["current"] = {"schema": "szl.research-anatomy.v1", "nodes": []}
    document["observed_digests"] = {}
    return document


class EmptySnapshotTests(unittest.TestCase):
    def test_complete_retirement_preserves_all_missing_claims_and_edges(self):
        document = retirement()
        before = copy.deepcopy(document)
        report = PLAN(document)
        self.assertEqual(document, before)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertEqual(report["removed_nodes"], ["conclusion", "data", "evaluation", "unrelated"])
        self.assertEqual(report["removed_edges"], [
            {"dependency": "evaluation", "dependent": "conclusion"},
            {"dependency": "data", "dependent": "evaluation"},
        ])
        self.assertEqual(report["recheck_order"], [])
        self.assertEqual(set(report["retired_affected_nodes"]), set(report["removed_nodes"]))
        self.assertEqual([row["id"] for row in report["claims"]], ["conclusion", "unrelated"])
        self.assertTrue(all(row["state"] == "MISSING_REQUIRED_CLAIM" for row in report["claims"]))
        self.assertTrue(all(row["truth_verified"] is False for row in report["claims"]))
        self.assertFalse(report["experiments_executed"])

    def test_two_empty_snapshots_still_report_required_claims_missing(self):
        document = retirement()
        document["baseline"] = copy.deepcopy(document["current"])
        report = PLAN(document)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertEqual(report["affected_nodes"], [])
        self.assertEqual(len(report["claims"]), len(document["required_claims"]))
        self.assertTrue(all(row["state"] == "MISSING_REQUIRED_CLAIM" for row in report["claims"]))

    def test_empty_baseline_reports_current_nodes_as_added(self):
        document = json.loads((SKILL / "assets/example.json").read_text(encoding="utf-8"))
        document["baseline"] = {"schema": "szl.research-anatomy.v1", "nodes": []}
        report = PLAN(document)
        self.assertEqual(set(report["added_nodes"]), {node["id"] for node in document["current"]["nodes"]})
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertTrue(all(row["state"] == "RECHECK_REQUIRED" for row in report["claims"]))

    def test_empty_snapshot_rejects_dangling_evidence_links(self):
        document = retirement()
        document["current"]["evidence_links"] = [{"claim": "conclusion", "evidence": "data", "relation": "supports"}]
        with self.assertRaises(ValueError):
            PLAN(document)

    def test_empty_snapshot_rejects_stale_readback_ids(self):
        document = retirement()
        document["observed_digests"] = {"data": "a" * 64}
        with self.assertRaises(ValueError):
            PLAN(document)

    def test_empty_required_claims_remain_invalid(self):
        document = retirement()
        document["required_claims"] = []
        with self.assertRaises(ValueError):
            PLAN(document)

    def test_cli_emits_retirement_report_without_erasing_missing_claims(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "retirement.json"
            path.write_text(json.dumps(retirement()), encoding="utf-8")
            result = subprocess.run([sys.executable, "-B", str(SKILL / "scripts/run.py"), str(path)],
                                    capture_output=True, text=True, timeout=20, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "REVIEW_REQUIRED")
            self.assertEqual(len(report["claims"]), 2)
            self.assertTrue(all(row["state"] == "MISSING_REQUIRED_CLAIM" for row in report["claims"]))


if __name__ == "__main__":
    unittest.main()
