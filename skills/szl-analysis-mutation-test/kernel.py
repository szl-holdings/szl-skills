"""szl-analysis-mutation-test — would your QC have caught it?

Stdlib only. Offline. Never runs your pipeline.

Phase 1 (generate): take a tabular dataset (list of row objects) and produce
deliberately corrupted variants, one per mutation class, each with a manifest
that records exactly what was changed. The scientist (or the agent) runs the
project's own quality checks on every variant and records whether each check
flagged the variant.

Phase 2 (score): compare the recorded outcomes against the manifest and report
which mutation classes were CAUGHT, MISSED or NOT_TESTED. Coverage is reported
as a count, never as a quality score.

The idea is borrowed from szl-eclipse (mutation testing for receipt verifiers):
a verifier that has never been shown a corrupted input has never been tested.

Mutation classes (applied to a deep copy of the rows):
  duplicate_rows     append exact copies of k rows
  drop_rows          remove k rows
  shuffle_labels     permute the values of the label column (deterministic seed)
  scale_column       multiply one numeric column by 1000 (unit error)
  swap_columns       exchange two numeric columns' values
  inject_missing     set k cells of one column to null
  precision_drift    add 1e-7 to one numeric column
  type_confusion     stringify one numeric column
  reorder_rows       reverse row order
  truncate           keep only the first half of the rows

Opt-in property/boundary classes (PROPERTY_MUTATIONS) run only when named; they or
require_variant_binding give a v2 plan. Contract: references/contract.md.
"""
from __future__ import annotations

import copy
import hashlib
import json
import random

SCHEMA_PLAN = "szl.mutation-plan.v1"
SCHEMA_PLAN_V2 = "szl.mutation-plan.v2"
SCHEMA_REPORT = "szl.mutation-coverage-report.v1"
MUTATIONS = ["duplicate_rows", "drop_rows", "shuffle_labels", "scale_column", "swap_columns",
             "inject_missing", "precision_drift", "type_confusion", "reorder_rows", "truncate"]
PROPERTY_MUTATIONS = ["scale_column_inverse", "negate_column", "extreme_value",
                      "conflicting_duplicate_id", "all_missing_column", "single_row"]
EXTREME = 1.7976931348623157e308


def szl_canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def szl_digest(value) -> str:
    return hashlib.sha256(szl_canonical(value).encode()).hexdigest()


def _numeric_columns(rows):
    cols = {}
    for row in rows:
        for k, v in row.items():
            if isinstance(v, bool):
                continue
            cols.setdefault(k, True)
            if not isinstance(v, (int, float)) and v is not None:
                cols[k] = False
    return sorted(k for k, ok in cols.items() if ok)


def _apply(name, rows, label, k, rng):
    rows = copy.deepcopy(rows)
    numeric = [c for c in _numeric_columns(rows) if c != label]
    detail = {}
    if name == "duplicate_rows":
        picked = [rows[i] for i in sorted(rng.sample(range(len(rows)), min(k, len(rows))))]
        rows.extend(copy.deepcopy(picked)); detail = {"duplicated": len(picked)}
    elif name == "drop_rows":
        drop = set(rng.sample(range(len(rows)), min(k, max(len(rows) - 1, 0))))
        rows = [r for i, r in enumerate(rows) if i not in drop]; detail = {"dropped": sorted(drop)}
    elif name == "shuffle_labels":
        if label is None or not all(label in r for r in rows):
            return None, {"skipped": "no label column"}
        values = [r[label] for r in rows]
        shuffled = values[:]
        rng.shuffle(shuffled)
        if shuffled == values and len(set(map(str, values))) > 1:
            shuffled = values[1:] + values[:1]
        for r, v in zip(rows, shuffled):
            r[label] = v
        detail = {"column": label, "changed_cells": sum(1 for a, b in zip(values, shuffled) if a != b)}
    elif name in ("scale_column", "precision_drift", "type_confusion", "inject_missing"):
        if not numeric:
            return None, {"skipped": "no numeric column"}
        col = numeric[0]
        for i, r in enumerate(rows):
            if r.get(col) is None:
                continue
            if name == "scale_column":
                r[col] = r[col] * 1000
            elif name == "precision_drift":
                r[col] = r[col] + 1e-7
            elif name == "type_confusion":
                r[col] = str(r[col])
            elif name == "inject_missing" and i < k:
                r[col] = None
        detail = {"column": col, "factor": 1000 if name == "scale_column" else None,
                  "cells": min(k, len(rows)) if name == "inject_missing" else len(rows)}
    elif name == "swap_columns":
        if len(numeric) < 2:
            return None, {"skipped": "fewer than two numeric columns"}
        c1, c2 = numeric[0], numeric[1]
        for r in rows:
            r[c1], r[c2] = r.get(c2), r.get(c1)
        detail = {"columns": [c1, c2]}
    elif name == "reorder_rows":
        rows.reverse(); detail = {"order": "reversed"}
    elif name == "truncate":
        rows = rows[: max(1, len(rows) // 2)]; detail = {"kept": len(rows)}
    else:
        raise ValueError("unknown mutation %r" % name)
    return rows, detail


def _apply_property(name, rows, label, rng, columns, idc):
    rows = copy.deepcopy(rows)
    if name == "single_row":
        return rows[:1], {"kept": 1}
    if name == "conflicting_duplicate_id" and idc is None:
        return None, {"skipped": "no id_column"}
    num = _numeric_columns(rows)
    cols = [c for c in columns if c != idc] if columns else [c for c in num if c not in (label, idc)][:1]
    bad = [c for c in cols if c not in num and name != "all_missing_column"]
    if bad or not cols:
        return None, {"skipped": "not numeric: %s" % bad if bad else "no target column"}
    if name == "conflicting_duplicate_id":
        ok = [i for i, r in enumerate(rows) if r.get(idc) is not None
              and all(type(r.get(c)) in (int, float) and r[c] + 1 != r[c] for c in cols)]
        if not ok:
            return None, {"skipped": "no row with an id and numeric targets"}
        i = rng.choice(ok)
        rows.append({**rows[i], **{c: rows[i][c] + 1 for c in cols}})
        return rows, {"id_column": idc, "row": i, "columns": cols, "delta": 1}
    detail = {"columns": cols, "cells": 0}
    for c in cols:
        if name == "extreme_value":
            i = rng.choice([j for j, r in enumerate(rows) if c in r])
            rows[i][c] = EXTREME; detail.setdefault("row_indices", []).append(i); detail["cells"] += 1
            continue
        for r in rows:
            v = r.get(c)
            if name == "all_missing_column" and v is not None:
                r[c] = None
            elif name != "all_missing_column" and type(v) in (int, float) and v and abs(v) <= EXTREME:
                r[c] = -v if name == "negate_column" else v / 1000  # bound: v / 1000 must not overflow
            else:
                continue
            detail["cells"] += 1
    if name == "extreme_value":
        detail["value"] = EXTREME
    elif name != "all_missing_column":
        detail["factor"] = -1 if name == "negate_column" else 0.001
    return rows, detail


def _names(v):
    return isinstance(v, list) and all(isinstance(x, str) and x for x in v) and len(set(v)) == len(v)


def _invalid(doc, rows):
    sel, cols, idc = doc.get("mutations"), doc.get("columns"), doc.get("id_column")
    if type(doc.get("k", 0)) is not int or doc.get("k", 0) < 0 or type(doc.get("seed", 0)) is not int:
        return "k (>= 0) and seed must be integers"
    if sel is not None and not _names(sel):
        return "mutations must be distinct names"
    if not (cols is None or cols and _names(cols)) or not (idc is None or _names([idc])):
        return "columns/id_column must be distinct non-empty names"
    if (cols or idc) and not set(sel or []) & set(PROPERTY_MUTATIONS):
        return "columns/id_column need an opt-in property class"
    if any(not any(c in r for r in rows) for c in (cols or []) + ([idc] if idc else [])):
        return "columns must be present in rows"
    if not isinstance(doc.get("require_variant_binding", False), bool):
        return "require_variant_binding must be true or false"
    return None


def szl_generate_mutations(document) -> dict:
    if not isinstance(document, dict) or not isinstance(document.get("rows"), list) or not document["rows"]:
        return {"schema": SCHEMA_PLAN, "status": "ERROR", "reason": "document.rows must be a non-empty list of objects"}
    rows = document["rows"]
    if not all(isinstance(r, dict) for r in rows):
        return {"schema": SCHEMA_PLAN, "status": "ERROR", "reason": "every row must be an object"}
    invalid = _invalid(document, rows)
    if invalid:
        return {"schema": SCHEMA_PLAN, "status": "ERROR", "reason": invalid}
    label = document.get("label_column")
    k = document.get("k", 2)
    seed = document.get("seed", 11)
    selected = document.get("mutations") or MUTATIONS
    unknown = [m for m in selected if m not in MUTATIONS + PROPERTY_MUTATIONS]
    if unknown:
        return {"schema": SCHEMA_PLAN, "status": "ERROR", "reason": "unknown mutations: %s" % unknown}
    columns, id_column = document.get("columns"), document.get("id_column")
    baseline = szl_digest(rows)
    variants = []
    for name in selected:
        rng = random.Random(f"{seed}:{name}")
        if name in PROPERTY_MUTATIONS:
            mutated, detail = _apply_property(name, rows, label, rng, columns, id_column)
        else:
            mutated, detail = _apply(name, rows, label, k, rng)
        if mutated is None:
            variants.append({"mutation": name, "status": "SKIPPED", "detail": detail, "rows": None, "sha256": None})
            continue
        d = szl_digest(mutated)
        variants.append({"mutation": name, "status": "GENERATED" if d != baseline else "NO_OP",
                         "detail": detail, "rows": mutated, "sha256": d, "row_count": len(mutated)})
    plan = {"schema": SCHEMA_PLAN, "status": "GENERATED", "seed": seed, "baseline_sha256": baseline,
            "baseline_rows": len(rows), "label_column": label,
            "expected_detection": [v["mutation"] for v in variants if v["status"] == "GENERATED"],
            "variants": variants,
            "scope": "Synthetic corruptions of the supplied rows. The pipeline under test is never executed by this helper."}
    bind = document.get("require_variant_binding", False)
    if bind or set(selected) & set(PROPERTY_MUTATIONS):
        plan.update(schema=SCHEMA_PLAN_V2, require_variant_binding=bind, columns=columns, id_column=id_column)
    return plan


def szl_score_mutations(plan, outcomes) -> dict:
    """outcomes: {"baseline_flagged": false, "variants": {mutation: {"flagged": bool, "check": str}}}"""
    if not isinstance(plan, dict) or plan.get("schema") not in (SCHEMA_PLAN, SCHEMA_PLAN_V2) or not isinstance(plan.get("variants"), list):
        return {"schema": SCHEMA_REPORT, "status": "ERROR", "reason": "plan must be a szl.mutation-plan.v1/v2 document"}
    if not isinstance(outcomes, dict) or not isinstance(outcomes.get("variants"), dict):
        return {"schema": SCHEMA_REPORT, "status": "ERROR", "reason": "outcomes.variants must map mutation -> {flagged: bool}"}
    names = [v.get("mutation") if isinstance(v, dict) else None for v in plan["variants"]]
    if not all(isinstance(n, str) for n in names) or len(set(names)) != len(names):
        return {"schema": SCHEMA_REPORT, "status": "ERROR", "reason": "plan variants need distinct mutation names"}
    if not all(isinstance(d.get("require_variant_binding", False), bool) for d in (plan, outcomes)):
        return {"schema": SCHEMA_REPORT, "status": "ERROR", "reason": "require_variant_binding must be true or false"}
    bind = plan.get("require_variant_binding") is True or outcomes.get("require_variant_binding") is True
    rows, caught, missed, untested = [], [], [], []
    for v in plan["variants"]:
        name = v["mutation"]
        if v["status"] != "GENERATED":
            rows.append({"mutation": name, "result": "NOT_APPLICABLE", "reason": v["status"]}); continue
        o = outcomes["variants"].get(name)
        if not isinstance(o, dict) or not isinstance(o.get("flagged"), bool):
            rows.append({"mutation": name, "result": "NOT_TESTED"}); untested.append(name); continue
        if o.get("variant_sha256") not in (None, v["sha256"]):
            rows.append({"mutation": name, "result": "NOT_TESTED", "reason": "outcome refers to a different variant digest"}); untested.append(name); continue
        if bind and o.get("variant_sha256") is None:
            rows.append({"mutation": name, "result": "NOT_TESTED", "reason": "variant_sha256 required"}); untested.append(name); continue
        result = "CAUGHT" if o["flagged"] else "MISSED"
        (caught if o["flagged"] else missed).append(name)
        rows.append({"mutation": name, "result": result, "check": o.get("check"), "detail": v["detail"]})
    baseline_flagged = outcomes.get("baseline_flagged")
    status = "SCORED"
    if baseline_flagged is True:
        status = "BASELINE_FLAGGED"  # the checks reject the untouched data; detection counts are not interpretable
    elif untested and not caught and not missed:
        status = "NO_OUTCOMES_RECORDED"
    elif baseline_flagged is not False:
        status = "BASELINE_NOT_TESTED"  # an always-reject QC would otherwise score full coverage
    reason = ({"reason": "declare baseline_flagged false after checking the untouched data"}
              if status == "BASELINE_NOT_TESTED" else {})
    return {"schema": SCHEMA_REPORT, "status": status, "baseline_flagged": baseline_flagged,
            "tested": len(caught) + len(missed), "caught": caught, "missed": missed, "not_tested": untested,
            "coverage": f"{len(caught)}/{len(caught) + len(missed) + len(untested)}",
            "rows": rows, "plan_sha256": szl_digest(plan),
            "scope": "Detection of the listed synthetic corruptions by the checks the user ran and reported. "
                     "Not a measure of pipeline correctness or of real-world error rates.", **reason}
