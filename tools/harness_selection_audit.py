#!/usr/bin/env python3
"""Offline, fail-closed audit of adaptive harness-selection evidence.

Reads a bounded self-reported JSON record. No candidate code, model, grader,
dataset, shell command, provider API, or external executable is ever run.
Even a good record is NOT an independent witness or release authorization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

SCHEMA = "szl.harness-selection-audit/v1"
RESULT_SCHEMA = "szl.harness-selection-audit-result/v1"
MAX_BYTES = 262_144
MAX_ROUNDS = 100
MAX_CANDIDATES_PER_ROUND = 16
MAX_ITEMS = 2048
HEX = re.compile(r"[a-f0-9]{64}\Z")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
SOURCE = re.compile(r"[a-f0-9]{40}\Z")


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def exact(value: object, keys: set[str], label: str) -> dict:
    require(type(value) is dict and set(value) == keys, label + "_shape_invalid")
    return value


def sha(value: object, field: str) -> str:
    require(
        type(value) is str and HEX.fullmatch(value) is not None,
        field + "_invalid_sha256",
    )
    return value


def boolean(value: object, field: str) -> bool:
    require(type(value) is bool, field + "_must_be_boolean")
    return value


def number(value: object, field: str, low: float, high: float) -> float:
    require(type(value) in (int, float), field + "_must_be_number")
    # Check bounds before converting to float: huge but legal JSON integers
    # otherwise raise OverflowError inside math.isfinite and escape the CLI.
    require(low <= value <= high, field + "_out_of_bounds")
    parsed = float(value)
    require(math.isfinite(parsed), field + "_out_of_bounds")
    return parsed


def positive_int(value: object, field: str, cap: int) -> int:
    require(type(value) is int and 1 <= value <= cap, field + "_invalid_count")
    return value


def _pairs(items):
    out = {}
    for key, value in items:
        require(key not in out, "duplicate_json_key")
        out[key] = value
    return out


def _no_constant(_):
    raise ValueError("non_finite_json")


def _finite_float(text: str) -> float:
    value = float(text)
    require(math.isfinite(value), "non_finite_number")
    return value


def strict_json(raw: bytes) -> object:
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "invalid_input_size")
    try:
        return json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_constant=_no_constant,
            parse_float=_finite_float,
        )
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("invalid_json_encoding_or_depth") from exc


def exact_binomial_tail(candidate_only: int, baseline_only: int) -> float:
    """Pr[Binom(discordant, 1/2) >= observed candidate-only], one-sided.

    The paired entries must derive from a truly independent, once-only final
    test for this nominal p-value to have its intended interpretation.
    """
    count = candidate_only + baseline_only
    if count == 0:
        return 1.0
    return sum(math.comb(count, k) for k in range(candidate_only, count + 1)) / (
        1 << count
    )


def audit(raw: bytes) -> dict:
    payload_sha = hashlib.sha256(raw).hexdigest()
    data = exact(
        strict_json(raw),
        {
            "schema",
            "study_id",
            "source_revision",
            "grader",
            "pre_registration",
            "development",
            "sealed_test",
        },
        "root",
    )
    require(data["schema"] == SCHEMA, "unknown_schema")
    require(
        type(data["study_id"]) is str and ID.fullmatch(data["study_id"]) is not None,
        "invalid_study_id",
    )
    require(
        type(data["source_revision"]) is str
        and SOURCE.fullmatch(data["source_revision"]) is not None,
        "mutable_source_revision",
    )

    grader = exact(
        data["grader"], {"sha256", "frozen_before_search", "controls"}, "grader"
    )
    grader_hash = sha(grader["sha256"], "grader")
    frozen = boolean(grader["frozen_before_search"], "grader_frozen")
    controls = grader["controls"]
    require(
        type(controls) is list and 2 <= len(controls) <= 32, "control_count_invalid"
    )
    seen_ids, expected_types = set(), set()
    controls_passed = True
    for row in controls:
        c = exact(row, {"id", "expected", "observed"}, "control")
        require(
            type(c["id"]) is str and ID.fullmatch(c["id"]) is not None,
            "control_id_invalid",
        )
        require(c["id"] not in seen_ids, "duplicate_control")
        seen_ids.add(c["id"])
        require(
            c["expected"] in ("ACCEPT", "REJECT") and type(c["expected"]) is str,
            "invalid_control_expectation",
        )
        require(
            type(c["observed"]) is str and c["observed"] in ("ACCEPT", "REJECT"),
            "invalid_control_observation",
        )
        expected_types.add(c["expected"])
        controls_passed &= c["expected"] == c["observed"]
    require(
        expected_types == {"ACCEPT", "REJECT"},
        "positive_and_negative_controls_required",
    )

    rules = exact(
        data["pre_registration"],
        {"alpha", "minimum_effect", "minimum_final_rows", "max_prior_loss_rate"},
        "pre_registration",
    )
    alpha = number(rules["alpha"], "alpha", 0.001, 0.05)
    effect_floor = number(rules["minimum_effect"], "minimum_effect", 0.001, 1.0)
    min_rows = positive_int(
        rules["minimum_final_rows"], "minimum_final_rows", MAX_ITEMS
    )
    require(min_rows >= 30, "minimum_final_rows_too_small")
    max_prior_loss = number(rules["max_prior_loss_rate"], "max_prior_loss_rate", 0, 1)
    require(max_prior_loss < 1, "prior_loss_gate_cannot_be_disabled")

    dev = exact(
        data["development"], {"dataset_sha256", "rows", "rounds"}, "development"
    )
    dev_hash = sha(dev["dataset_sha256"], "development_dataset")
    n_dev = positive_int(dev["rows"], "development_rows", MAX_ITEMS)
    rounds = dev["rounds"]
    require(
        type(rounds) is list and 1 <= len(rounds) <= MAX_ROUNDS, "round_count_invalid"
    )
    all_candidate_ids = set()
    all_snapshots = []
    by_candidate_id = {}
    score_sets_by_hash = {}
    for index, rnd in enumerate(rounds, start=1):
        r = exact(rnd, {"round", "candidates"}, "round")
        require(type(r["round"]) is int and r["round"] == index, "nonsequential_round")
        candidates = r["candidates"]
        require(
            type(candidates) is list
            and 1 <= len(candidates) <= MAX_CANDIDATES_PER_ROUND,
            "candidate_count_invalid",
        )
        local = set()
        for cand in candidates:
            c = exact(
                cand,
                {"candidate_id", "harness_sha256", "correct", "total"},
                "candidate",
            )
            require(
                type(c["candidate_id"]) is str
                and ID.fullmatch(c["candidate_id"]) is not None,
                "invalid_candidate_id",
            )
            require(c["candidate_id"] not in local, "duplicate_candidate_id_per_round")
            local.add(c["candidate_id"])
            all_candidate_ids.add(c["candidate_id"])
            digest = sha(c["harness_sha256"], "harness")
            old_hash = by_candidate_id.setdefault(c["candidate_id"], digest)
            require(old_hash == digest, "candidate_id_rebound_to_different_hash")
            require(
                type(c["total"]) is int and c["total"] == n_dev, "denominator_mismatch"
            )
            require(
                type(c["correct"]) is int and 0 <= c["correct"] <= n_dev,
                "invalid_correct_count",
            )
            all_snapshots.append(digest)
            score_sets_by_hash.setdefault(digest, set()).add(c["correct"])
    require(len(set(all_snapshots)) >= 1, "no_candidate_snapshots")
    # Same hash, same grader, same dataset but changed scores might be
    # stochastic; absent a declared decoding/seed trace, it cannot qualify.
    drifting_scores = sum(len(scores) > 1 for scores in score_sets_by_hash.values())

    seal = exact(
        data["sealed_test"],
        {
            "dataset_sha256",
            "grader_sha256",
            "frozen_before_search",
            "exposed_to_proposer",
            "used_in_selection",
            "evaluation_count",
            "selected_harness_sha256",
            "baseline_harness_sha256",
            "matched_inference_budget",
            "previous_coverage",
            "total",
            "paired",
        },
        "sealed_test",
    )
    final_hash = sha(seal["dataset_sha256"], "sealed_dataset")
    seal_grader_hash = sha(seal["grader_sha256"], "sealed_grader")
    selected = sha(seal["selected_harness_sha256"], "selected_harness")
    baseline = sha(seal["baseline_harness_sha256"], "baseline_harness")
    budget_matched = boolean(seal["matched_inference_budget"], "matched_budget")
    prior = exact(
        seal["previous_coverage"],
        {"previously_solved", "retained"},
        "previous_coverage",
    )
    covered = positive_int(prior["previously_solved"], "previously_solved", MAX_ITEMS)
    require(
        type(prior["retained"]) is int and 0 <= prior["retained"] <= covered,
        "invalid_prior_coverage",
    )
    loss_rate = (covered - prior["retained"]) / covered
    require(
        type(seal["evaluation_count"]) is int and 0 <= seal["evaluation_count"] <= 100,
        "invalid_final_test_uses",
    )
    n = positive_int(seal["total"], "final_rows", MAX_ITEMS)
    paired = exact(
        seal["paired"],
        {
            "candidate_only",
            "baseline_only",
            "both_correct",
            "both_incorrect",
        },
        "paired",
    )
    for key, value in paired.items():
        require(type(value) is int and 0 <= value <= n, "invalid_paired_" + key)
    require(sum(paired.values()) == n, "paired_denominator_mismatch")
    sealed = boolean(seal["frozen_before_search"], "sealed_before_search")
    exposed = boolean(seal["exposed_to_proposer"], "exposure")
    selected_on_test = boolean(seal["used_in_selection"], "test_selection")
    candidate_only = paired["candidate_only"]
    baseline_only = paired["baseline_only"]
    candidate_correct = candidate_only + paired["both_correct"]
    baseline_correct = baseline_only + paired["both_correct"]
    difference = (candidate_only - baseline_only) / n
    p_value = exact_binomial_tail(candidate_only, baseline_only)

    problems = []
    if drifting_scores:
        problems.append("REPEATED_UNCHANGED_HARNESS_HAS_UNEXPLAINED_SCORE_DRIFT")
    if not frozen:
        problems.append("GRADER_NOT_FROZEN")
    if not controls_passed:
        problems.append("GRADER_NEGATIVE_CONTROL_FAILED")
    if final_hash == dev_hash:
        problems.append("SEALED_AND_DEVELOPMENT_DATA_IDENTICAL")
    if seal_grader_hash != grader_hash:
        problems.append("SEALED_GRADER_IDENTITY_MISMATCH")
    if not sealed:
        problems.append("FINAL_TEST_NOT_FROZEN")
    if exposed:
        problems.append("FINAL_TEST_EXPOSED_TO_PROPOSER")
    if selected_on_test:
        problems.append("FINAL_TEST_USED_TO_SELECT_HARNESS")
    if seal["evaluation_count"] != 1:
        problems.append("FINAL_TEST_NOT_USED_EXACTLY_ONCE")
    if selected not in all_snapshots:
        problems.append("SELECTED_HARNESS_NOT_EVALUATED_ON_DEVELOPMENT")
    if baseline not in all_snapshots:
        problems.append("BASELINE_HARNESS_NOT_EVALUATED_ON_DEVELOPMENT")
    if selected == baseline:
        problems.append("IDENTICAL_BASELINE_AND_CANDIDATE_HARNESS")
    if not budget_matched:
        problems.append("BASELINE_AND_CANDIDATE_BUDGET_NOT_MATCHED")
    if loss_rate > max_prior_loss:
        problems.append("PREVIOUSLY_SOLVED_TASK_REGRESSION_EXCEEDS_LIMIT")
    if n < min_rows:
        problems.append("FINAL_SAMPLE_BELOW_PREREGISTERED_MINIMUM")
    if difference < effect_floor:
        problems.append("NOMINAL_EFFECT_BELOW_PREREGISTRATION")
    if p_value > alpha:
        problems.append("NOMINAL_ONE_SIDED_P_ABOVE_ALPHA")
    # A successful self-report remains unauthenticated; independent replay is required.
    problems.append("NO_INDEPENDENT_INPUT_OR_EXECUTION_WITNESS")

    return {
        "schema": RESULT_SCHEMA,
        "study_id": data["study_id"],
        "source_revision": data["source_revision"],
        "input_sha256": payload_sha,
        "state": (
            "UNATTESTED_REVIEW_CANDIDATE"
            if problems == ["NO_INDEPENDENT_INPUT_OR_EXECUTION_WITNESS"]
            else "HOLD_INCOMPLETE_OR_CONTRADICTORY_EVIDENCE"
        ),
        "release_authorized": False,
        "model_quality_established": False,
        "independently_verified": False,
        "production_ready": False,
        "selection": {
            "rounds": len(rounds),
            "candidate_evaluations": len(all_snapshots),
            "unique_harness_hashes": len(set(all_snapshots)),
            "same_harness_hashes_with_changed_scores": drifting_scores,
            "distinct_candidate_ids": len(all_candidate_ids),
            "development_reused_across_rounds": len(rounds) > 1,
            "development_is_final_test": final_hash == dev_hash,
            "reported_control_passes": sum(
                row["observed"] == row["expected"] for row in controls
            ),
            "reported_controls_total": len(controls),
        },
        "final_test": {
            "rows": n,
            "candidate_correct": candidate_correct,
            "baseline_correct": baseline_correct,
            "candidate_accuracy": candidate_correct / n,
            "baseline_accuracy": baseline_correct / n,
            "paired_difference": difference,
            "discordant_candidate_only": candidate_only,
            "discordant_baseline_only": baseline_only,
            "nominal_one_sided_exact_mcnemar_p": p_value,
            "nominal_alpha": alpha,
            "preregistered_minimum_effect": effect_floor,
            "baseline_harness_sha256": baseline,
            "selected_harness_sha256": selected,
            "reported_matched_inference_budget": budget_matched,
            "previously_solved": covered,
            "retained_previously_solved": prior["retained"],
            "previously_solved_loss_rate": loss_rate,
            "maximum_preregistered_prior_loss_rate": max_prior_loss,
            "interpretation": (
                "Self-reported, nominal paired one-sided test. "
                "Selection-set feedback is adaptive; only a truly "
                "unseen once-only final test can support this inference."
            ),
        },
        "hold_reasons": problems,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "--output", type=Path, help="write exclusively, never overwrite"
    )
    args = parser.parse_args(argv)
    try:
        if args.input.is_symlink() or not args.input.is_file():
            raise ValueError("input_not_regular_file")
        if args.input.stat().st_size > MAX_BYTES:
            raise ValueError("input_too_large")
        raw = args.input.read_bytes()
        result = audit(raw)
        serialized = (
            json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n"
        ).encode()
        if args.output is None:
            sys.stdout.buffer.write(serialized)
        else:
            if args.output.is_symlink():
                raise ValueError("output_symlink_refused")
            with args.output.open("xb") as out:
                out.write(serialized)
    except (ValueError, OSError, TypeError) as error:
        print(
            "AUDIT_UNAVAILABLE:" + type(error).__name__ + ":" + str(error)[:100],
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
