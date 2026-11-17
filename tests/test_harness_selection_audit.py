"""Bounded negative-control tests for offline adaptive harness audit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import harness_selection_audit as h

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    winner, loser = "e" * 64, "f" * 64
    return {
        "schema": h.SCHEMA,
        "study_id": "synthetic-study-001",
        "source_revision": "a" * 40,
        "grader": {
            "sha256": "b" * 64,
            "frozen_before_search": True,
            "controls": [
                {"id": "positive", "expected": "ACCEPT", "observed": "ACCEPT"},
                {"id": "poisoned", "expected": "REJECT", "observed": "REJECT"},
            ],
        },
        "pre_registration": {
            "alpha": 0.05,
            "minimum_effect": 0.10,
            "minimum_final_rows": 40,
            "max_prior_loss_rate": 0.05,
        },
        "development": {
            "dataset_sha256": "c" * 64,
            "rows": 50,
            "rounds": [
                {
                    "round": index,
                    "candidates": [
                        {
                            "candidate_id": f"trial-good-{index}",
                            "harness_sha256": winner if index == 3 else str(index) * 64,
                            "correct": 39 + index,
                            "total": 50,
                        },
                        {
                            "candidate_id": f"trial-bad-{index}",
                            "harness_sha256": loser
                            if index == 3
                            else str(index + 3) * 64,
                            "correct": 30 + index,
                            "total": 50,
                        },
                    ],
                }
                for index in range(1, 4)
            ],
        },
        "sealed_test": {
            "dataset_sha256": "d" * 64,
            "grader_sha256": "b" * 64,
            "frozen_before_search": True,
            "exposed_to_proposer": False,
            "used_in_selection": False,
            "evaluation_count": 1,
            "selected_harness_sha256": winner,
            "baseline_harness_sha256": loser,
            "matched_inference_budget": True,
            "previous_coverage": {"previously_solved": 40, "retained": 40},
            "total": 80,
            "paired": {
                "candidate_only": 30,
                "baseline_only": 7,
                "both_correct": 32,
                "both_incorrect": 11,
            },
        },
    }


def wire(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")


def report(value=None):
    return h.audit(wire(fixture() if value is None else value))


class AuditTests(unittest.TestCase):
    def test_reviewable_record_never_claims_quality_or_authority(self):
        r = report()
        self.assertEqual(r["state"], "UNATTESTED_REVIEW_CANDIDATE")
        self.assertEqual(
            r["hold_reasons"], ["NO_INDEPENDENT_INPUT_OR_EXECUTION_WITNESS"]
        )
        self.assertEqual(r["selection"]["candidate_evaluations"], 6)
        self.assertEqual(r["selection"]["unique_harness_hashes"], 6)
        self.assertEqual(r["selection"]["same_harness_hashes_with_changed_scores"], 0)
        self.assertEqual(r["selection"]["reported_control_passes"], 2)
        self.assertAlmostEqual(r["final_test"]["paired_difference"], 23 / 80)
        self.assertLess(r["final_test"]["nominal_one_sided_exact_mcnemar_p"], 0.05)
        for key in (
            "release_authorized",
            "model_quality_established",
            "independently_verified",
            "production_ready",
        ):
            self.assertIs(r[key], False)

    def test_unchanged_harness_cannot_fake_round_improvement(self):
        data = fixture()
        first = data["development"]["rounds"][0]["candidates"][0]
        second = data["development"]["rounds"][1]["candidates"][0]
        second["harness_sha256"] = first["harness_sha256"]
        result = report(data)
        self.assertEqual(result["state"], "HOLD_INCOMPLETE_OR_CONTRADICTORY_EVIDENCE")
        self.assertIn(
            "REPEATED_UNCHANGED_HARNESS_HAS_UNEXPLAINED_SCORE_DRIFT",
            result["hold_reasons"],
        )
        self.assertEqual(
            result["selection"]["same_harness_hashes_with_changed_scores"], 1
        )

    def test_identical_repeated_harness_same_score_is_not_invented_progress(self):
        data = fixture()
        first = data["development"]["rounds"][0]["candidates"][0]
        second = data["development"]["rounds"][1]["candidates"][0]
        second["harness_sha256"] = first["harness_sha256"]
        second["correct"] = first["correct"]
        result = report(data)
        self.assertEqual(result["selection"]["unique_harness_hashes"], 5)
        self.assertEqual(
            result["selection"]["same_harness_hashes_with_changed_scores"], 0
        )
        self.assertEqual(result["state"], "UNATTESTED_REVIEW_CANDIDATE")

    def test_reusing_candidate_id_for_new_bytes_is_invalid(self):
        data = fixture()
        first_id = data["development"]["rounds"][0]["candidates"][0]["candidate_id"]
        data["development"]["rounds"][1]["candidates"][0]["candidate_id"] = first_id
        with self.assertRaisesRegex(ValueError, "candidate_id_rebound"):
            report(data)

    def test_extreme_json_integer_rejected_without_overflow(self):
        data = fixture()
        data["pre_registration"]["alpha"] = 10**2000
        with self.assertRaisesRegex(ValueError, "alpha_out_of_bounds"):
            report(data)

    def test_nominal_exact_tail_known_small_cases(self):
        self.assertEqual(h.exact_binomial_tail(0, 0), 1.0)
        self.assertEqual(h.exact_binomial_tail(1, 1), 0.75)
        self.assertEqual(h.exact_binomial_tail(2, 0), 0.25)
        self.assertEqual(h.exact_binomial_tail(0, 2), 1.0)
        self.assertEqual(h.exact_binomial_tail(5, 0), 1 / 32)

    def assert_hold(self, mutation, reason):
        data = fixture()
        mutation(data)
        res = report(data)
        self.assertIn(reason, res["hold_reasons"])
        self.assertEqual(res["state"], "HOLD_INCOMPLETE_OR_CONTRADICTORY_EVIDENCE")
        self.assertIs(res["release_authorized"], False)

    def test_frozen_grader_and_negative_controls(self):
        self.assert_hold(
            lambda d: d["grader"].update(frozen_before_search=False),
            "GRADER_NOT_FROZEN",
        )
        self.assert_hold(
            lambda d: d["grader"]["controls"][1].update(observed="ACCEPT"),
            "GRADER_NEGATIVE_CONTROL_FAILED",
        )

    def test_adaptive_test_set_reuse_and_leaks_hold(self):
        self.assert_hold(
            lambda d: d["sealed_test"].update(
                dataset_sha256=d["development"]["dataset_sha256"]
            ),
            "SEALED_AND_DEVELOPMENT_DATA_IDENTICAL",
        )
        self.assert_hold(
            lambda d: d["sealed_test"].update(grader_sha256="9" * 64),
            "SEALED_GRADER_IDENTITY_MISMATCH",
        )
        self.assert_hold(
            lambda d: d["sealed_test"].update(frozen_before_search=False),
            "FINAL_TEST_NOT_FROZEN",
        )
        self.assert_hold(
            lambda d: d["sealed_test"].update(exposed_to_proposer=True),
            "FINAL_TEST_EXPOSED_TO_PROPOSER",
        )
        self.assert_hold(
            lambda d: d["sealed_test"].update(used_in_selection=True),
            "FINAL_TEST_USED_TO_SELECT_HARNESS",
        )
        for count in (0, 2, 41):
            self.assert_hold(
                lambda d, x=count: d["sealed_test"].update(evaluation_count=x),
                "FINAL_TEST_NOT_USED_EXACTLY_ONCE",
            )

    def test_selected_harness_must_appear_in_development(self):
        self.assert_hold(
            lambda d: d["sealed_test"].update(selected_harness_sha256="9" * 64),
            "SELECTED_HARNESS_NOT_EVALUATED_ON_DEVELOPMENT",
        )

    def test_regression_and_budget_negative_controls(self):
        self.assert_hold(
            lambda d: d["sealed_test"].update(matched_inference_budget=False),
            "BASELINE_AND_CANDIDATE_BUDGET_NOT_MATCHED",
        )
        self.assert_hold(
            lambda d: d["sealed_test"]["previous_coverage"].update(retained=30),
            "PREVIOUSLY_SOLVED_TASK_REGRESSION_EXCEEDS_LIMIT",
        )
        self.assert_hold(
            lambda d: d["sealed_test"].update(baseline_harness_sha256="9" * 64),
            "BASELINE_HARNESS_NOT_EVALUATED_ON_DEVELOPMENT",
        )
        self.assert_hold(
            lambda d: d["sealed_test"].update(baseline_harness_sha256="e" * 64),
            "IDENTICAL_BASELINE_AND_CANDIDATE_HARNESS",
        )

    def test_insufficient_sample_effect_and_p_value_hold(self):
        self.assert_hold(
            lambda d: d["pre_registration"].update(minimum_final_rows=100),
            "FINAL_SAMPLE_BELOW_PREREGISTERED_MINIMUM",
        )

        def small_effect(d):
            d["sealed_test"]["paired"] = {
                "candidate_only": 8,
                "baseline_only": 10,
                "both_correct": 50,
                "both_incorrect": 12,
            }

        self.assert_hold(small_effect, "NOMINAL_EFFECT_BELOW_PREREGISTRATION")
        self.assert_hold(small_effect, "NOMINAL_ONE_SIDED_P_ABOVE_ALPHA")

    def test_no_discordance_is_not_significance(self):
        d = fixture()
        d["sealed_test"]["paired"] = {
            "candidate_only": 0,
            "baseline_only": 0,
            "both_correct": 65,
            "both_incorrect": 15,
        }
        self.assertEqual(
            report(d)["final_test"]["nominal_one_sided_exact_mcnemar_p"], 1.0
        )

    def test_immutable_input_identity_is_recorded(self):
        raw = wire(fixture())
        self.assertEqual(h.audit(raw)["input_sha256"], hashlib.sha256(raw).hexdigest())

    def test_no_provider_network_access(self):
        with patch.object(
            socket.socket, "connect", side_effect=AssertionError("network prohibited")
        ):
            self.assertEqual(report()["selection"]["rounds"], 3)

    def test_invalid_shape_and_number_refused(self):
        faults = [
            lambda d: d.update(source_revision="main"),
            lambda d: d.update(extra="tool to execute"),
            lambda d: d["grader"]["controls"].append(
                {"id": "positive", "expected": "ACCEPT", "observed": "ACCEPT"}
            ),
            lambda d: d["grader"].update(
                controls=[
                    {"id": "only-pos", "expected": "ACCEPT", "observed": "ACCEPT"},
                    {"id": "also-pos", "expected": "ACCEPT", "observed": "ACCEPT"},
                ]
            ),
            lambda d: d["pre_registration"].update(alpha=0),
            lambda d: d["pre_registration"].update(alpha=True),
            lambda d: d["pre_registration"].update(minimum_effect=0),
            lambda d: d["pre_registration"].update(max_prior_loss_rate=1),
            lambda d: d["sealed_test"]["previous_coverage"].update(retained=True),
            lambda d: d["sealed_test"]["previous_coverage"].update(previously_solved=0),
            lambda d: d["pre_registration"].update(minimum_final_rows=1),
            lambda d: d["development"].update(rows=True),
            lambda d: d["development"]["rounds"][1].update(round=1),
            lambda d: d["development"]["rounds"][0]["candidates"][0].update(correct=51),
            lambda d: d["development"]["rounds"][0]["candidates"][0].update(total=49),
            lambda d: d["development"]["rounds"][0]["candidates"][0].update(
                correct=5.5
            ),
            lambda d: d["development"]["rounds"][0]["candidates"][1].update(
                candidate_id=d["development"]["rounds"][0]["candidates"][0][
                    "candidate_id"
                ]
            ),
            lambda d: d["sealed_test"]["paired"].update(both_correct=33),
            lambda d: d["sealed_test"]["paired"].update(candidate_only=True),
            lambda d: d["sealed_test"].update(evaluation_count=True),
            lambda d: d["sealed_test"].update(selected_harness_sha256="missing"),
        ]
        for fault in faults:
            with self.subTest(fault=repr(fault)):
                data = fixture()
                fault(data)
                with self.assertRaises(ValueError):
                    report(data)

    def test_duplicate_nonfinite_and_bytes_refused(self):
        for raw in (
            b'{"study_id":"a","study_id":"b"}',
            b'{"x":NaN}',
            b'{"x":Infinity}',
            b'{"x":1e9999}',
            b"\xff",
            b" " * (h.MAX_BYTES + 1),
        ):
            with self.subTest(raw=raw[:15]):
                with self.assertRaises(ValueError):
                    h.audit(raw)

    def test_harness_selection_does_not_mutate_input_or_reuse_output_file(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "in.json"
            target = folder / "out.json"
            source.write_bytes(wire(fixture()))
            command = [
                sys.executable,
                "-B",
                str(ROOT / "tools/harness_selection_audit.py"),
                str(source),
                "--output",
                str(target),
            ]
            first = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=20)
            self.assertEqual(first.returncode, 0, first.stderr)
            stored = target.read_bytes()
            self.assertEqual(json.loads(stored)["state"], "UNATTESTED_REVIEW_CANDIDATE")
            self.assertEqual(source.read_bytes(), wire(fixture()))
            second = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=20)
            self.assertEqual(second.returncode, 2)
            self.assertEqual(target.read_bytes(), stored)

    def test_unknown_input_suppresses_report_output(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "bad.json"
            target = folder / "missing.json"
            source.write_bytes(b'{"schema":NaN}')
            run = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "tools/harness_selection_audit.py"),
                    str(source),
                    "--output",
                    str(target),
                ],
                cwd=ROOT,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(run.returncode, 2)
            self.assertFalse(target.exists())
            self.assertIn(b"AUDIT_UNAVAILABLE", run.stderr)


if __name__ == "__main__":
    unittest.main()
