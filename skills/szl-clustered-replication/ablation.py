# SPDX-License-Identifier: Apache-2.0
"""Original clustered ablation planner/analyzer over the unchanged v1 audit."""
import pathlib
import runpy
from decimal import Decimal

K = runpy.run_path(str(pathlib.Path(__file__).resolve().with_name("kernel.py")))
OBJ, AUDIT, DIGEST = K["szl_cluster_object"], K["szl_cluster_audit"], K["canonical_sha256"]
ABLATION_SCHEMA = "szl.clustered-ablation.v1"
SHARED = K["PLAN_KEYS"] - {"schema", "expected_cases"}
HONEST = {"independence_verified": False, "plan_timing_verified": False,
          "scientific_validity": "NOT_ESTABLISHED", "production_authority": "NONE"}


def szl_ablation_ids(values, label):
    if (not isinstance(values, list) or len(set(values)) != len(values)
            or any(len(K["szl_cluster_id"](value, label)) > 64 for value in values)):
        raise ValueError(label + ": distinct identifiers of at most 64 characters")
    return frozenset(values)


def szl_ablation_v1(plan, cases, losses):
    rows = [dict(case, unit=plan["unit"], baseline_loss=b, candidate_loss=c) for case, b, c in zip(cases, *losses)]
    return {"plan": dict({k: plan[k] for k in SHARED}, schema=K["SCHEMA"], expected_cases=cases), "rows": rows}


def szl_cluster_ablation_plan(plan):
    """Validate factors and arms, then flag design gaps; reads no outcomes."""
    OBJ(plan, SHARED | {"schema", "factors", "arms"}, "plan")
    factors, arms = szl_ablation_ids(plan["factors"], "factors"), plan["arms"]
    if plan["schema"] != ABLATION_SCHEMA or not isinstance(arms, list) or not (
            0 < len(factors) < 16 and 1 < len(arms) < 17):
        raise ValueError("Unsupported schema, or not 1-15 factors and 2-16 arms")
    findings, seen, sets, planned = [], {frozenset(): "full"}, {}, 0
    for arm in arms:
        cases = OBJ(arm, {"id", "removes", "multi_factor_declared", "expected_cases"}, "arm")["expected_cases"]
        name, removed = arm["id"], szl_ablation_ids(arm["removes"], "removes")
        szl_ablation_ids([name], "arm id")
        if name in sets or not removed <= factors or type(arm["multi_factor_declared"]) is not bool or (
                name == "full" and removed):
            raise ValueError("Duplicate arm, undeclared factor, non-boolean declaration or altered full arm")
        # A zero-loss v1 audit validates shared fields and cases.
        AUDIT(szl_ablation_v1(plan, cases, ([0] * len(cases),) * 2), None)
        planned += len(cases)
        if planned > K["MAX_ROWS"]:
            raise ValueError("Over 10000 planned rows")
        sets[name] = {(case["id"], case["cluster"]) for case in cases}
        if name != "full":
            if removed in seen:
                findings.append({"finding": "DUPLICATE_ARM", "subject": name, "same_as": seen[removed]})
            if len(removed) > 1 and not arm["multi_factor_declared"]:
                findings.append({"finding": "UNDECLARED_MULTI_FACTOR_DIFFERENCE", "subject": name})
            seen.setdefault(removed, name)
    if "full" not in sets:
        raise ValueError("Plan needs a full arm")
    findings += [{"finding": "CASE_SETS_DIFFER", "subject": name} for name in sets if sets[name] != sets["full"]]
    findings += [{"finding": "FACTOR_NEVER_ABLATED", "subject": name} for name in plan["factors"]
                 if not any(name in removed for removed in seen)]
    return {"schema": ABLATION_SCHEMA, "status": "PLAN_INCOMPLETE" if findings else "PLAN_COMPLETE",
            "plan_sha256": DIGEST(plan), "findings": findings, **HONEST}


def szl_ablation_audit(document, expected):
    if not isinstance(document, dict) or set(document) - {"rows"} != {"plan"}:
        raise ValueError("document must have plan and optional rows")
    plan, report = document["plan"], szl_cluster_ablation_plan(document["plan"])
    if expected not in (None, report["plan_sha256"]):
        raise ValueError("Expected plan commitment does not match")
    report["plan_binding"] = "MATCHED_COMMITMENT" if expected else "UNBOUND"
    if "rows" not in document:
        return report
    rows, losses = document["rows"], {}
    clusters = {(arm["id"], case["id"]): case["cluster"] for arm in plan["arms"] for case in arm["expected_cases"]}
    if not isinstance(rows, list) or len(rows) > K["MAX_ROWS"]:
        raise ValueError("rows must be a list of at most 10000 entries")
    for row in rows:
        key = (OBJ(row, {"id", "cluster", "arm", "unit", "loss"}, "row")["arm"], row["id"])
        if key in losses or key not in clusters or clusters[key] != row["cluster"] or row["unit"] != plan["unit"]:
            raise ValueError("Unplanned, duplicate, reassigned or mismatched-unit row")
        K["szl_cluster_number"](row["loss"], "loss")
        losses[key] = row["loss"]
    report.update(observations=len(rows), arms=[], inference_blockers=None, multiplicity_handoff=None,
                  missing=[{"arm": arm, "id": case} for arm, case in clusters if (arm, case) not in losses])
    if report["status"] == "PLAN_COMPLETE" and report["missing"]:
        report["status"] = "HOLD"
    if report["status"] != "PLAN_COMPLETE":
        return report
    full, family = next(arm["expected_cases"] for arm in plan["arms"] if arm["id"] == "full"), []
    for arm in [arm for arm in plan["arms"] if arm["id"] != "full"]:
        # Improvement = arm loss - full loss.
        v1 = szl_ablation_v1(plan, full, [[losses[(name, case["id"])] for case in full] for name in (arm["id"], "full")])
        audit = AUDIT(v1, DIGEST(v1["plan"]) if expected else None)
        test, report["inference_blockers"] = audit["conditional_exact_test"], audit["inference_blockers"]
        report["arms"].append({"arm": arm["id"], "removes": arm["removes"], "audit": audit})
        report["findings"] += [{"finding": name, "subject": arm["id"]} for name in audit["findings"]]
        family.append({"id": arm["id"], "p_value": test and format(Decimal(test["p_two_sided"]), "f")})
    report["findings"] += [{"finding": name, "subject": None} for name in report["inference_blockers"]]
    report["status"] = "ANALYZED"
    report["multiplicity_handoff"] = {"skill": "szl-multiplicity-audit", "p_values_adjusted": False, "family": family}
    return report


def szl_cluster_ablation(document, expected_plan_sha256=None):
    """Planner report, plus per-arm analysis when rows are present."""
    try:
        return szl_ablation_audit(document, expected_plan_sha256)
    except (ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        return {"schema": ABLATION_SCHEMA, "status": "INPUT_ERROR", "error_type": type(error).__name__,
                "reason": str(error), "multiplicity_handoff": None, **HONEST}
