# SPDX-License-Identifier: Apache-2.0
"""szl.benchmark-parity/v1: compare declared benchmark evidence across systems; no scoring."""
import hashlib
import pathlib
import re
import runpy

UNIFORM = (("evaluation_rows", "evaluation_row_ids_sha256", "EVALUATION_ROWS_MISMATCH"),
           ("budget", "tuning_trials", "BUDGET_ASYMMETRY"), ("budget", "compute_budget", "BUDGET_ASYMMETRY"),
           ("seeds", "seed_count", "SEED_COUNT_MISMATCH"), ("scorer", "scorer_sha256", "SCORER_MISMATCH"))


def szl_parity_value(field, v):
    if v is None or field.endswith("sha256") and type(v) is str and re.fullmatch("[0-9a-f]{64}", v):
        return v
    if field == "compute_budget" and type(v) in (int, float) and v >= 0:
        return v
    if field in ("tuning_trials", "seed_count") and type(v) is int and (1 if field == "seed_count" else 0) <= v <= 10 ** 9:
        return v
    raise ValueError("invalid " + field)


def szl_audit_benchmark_parity(spec):
    """Absent evidence is UNKNOWN, never PARITY_OK."""
    k = runpy.run_path(str(pathlib.Path(__file__).resolve().with_name("kernel.py")))
    blob, name, names = k["szl_dataset_json_evidence"](spec), k["szl_dataset_name"], k["szl_dataset_names"]
    if type(spec) is not dict or spec.get("schema") != "szl.benchmark-parity/v1" or set(spec) - {
            "schema", "held_out", "training_splits", "rows", "systems", "declared_asymmetries", "compute_unit"}:
        raise ValueError("unsupported schema or field")
    systems, held, train, rows = (spec.get(f) for f in ("systems", "held_out", "training_splits", "rows"))
    declared = {} if spec.get("declared_asymmetries") is None else spec["declared_asymmetries"]
    fields = {"id", "role", "transforms"} | {f for _, f, _ in UNIFORM}
    if type(systems) is not list or not 2 <= len(systems) <= 64 or any(
            type(s) is not dict or set(s) - fields or s.get("role") not in ("baseline", "candidate") for s in systems):
        raise ValueError("systems: 2-64 contract objects with role baseline or candidate")
    by_id = dict(zip(names([s.get("id") for s in systems], "system id", maximum=64), systems))
    if type(declared) is not dict or set(declared) - {"tuning_trials", "compute_budget"}:
        raise ValueError("declared_asymmetries: tuning_trials or compute_budget only")
    for reason in declared.values():
        name(reason, "asymmetry reason")
    if train is not None:
        names(train, "training_splits", maximum=32)
    if spec.get("compute_unit") is not None:
        name(spec["compute_unit"], "compute_unit")
    if held is not None and (type(held) is not dict or set(held) != {"split", "row_ids_sha256"}
                             or name(held["split"], "held-out split") in (train or [])):
        raise ValueError("held_out: exactly split and row_ids_sha256, not a training split")
    if rows is not None:
        if type(rows) is not list or len(rows) > 100000 or any(type(r) is not dict or set(r) != {"id", "split"} for r in rows):
            raise ValueError("rows: at most 100000 objects with exactly id and split")
        names([r["id"] for r in rows], "row id", nonempty=False)
        for r in rows:
            name(r["split"], "row split")
    checks = {c: {"status": None, "findings": [], "missing_evidence": []}
              for c in ("evaluation_rows", "baseline", "budget", "seeds", "scorer", "preprocessing")}
    target = held and szl_parity_value("row_ids_sha256", held["row_ids_sha256"])
    if target is None:
        checks["evaluation_rows"]["missing_evidence"].append({"missing": "held_out.row_ids_sha256"})
    if all(s["role"] != "baseline" for s in systems):
        checks["baseline"]["findings"].append({"code": "NO_BASELINE"})
    applied = []
    for check, field, code in UNIFORM:
        c, known = checks[check], {}
        for sid, s in by_id.items():
            v = szl_parity_value(field, s.get(field))
            if v is None:
                c["missing_evidence"].append({"system": sid, "missing": field})
            else:
                known[sid] = v
        if field == "compute_budget" and known and spec.get("compute_unit") is None:
            c["missing_evidence"].append({"missing": "compute_unit"})
        if target and check == "evaluation_rows":
            off = [sid for sid in known if known[sid] != target]
        else:
            off = list(known) if len(set(known.values())) > 1 else []
        shown = {sid: by_id[sid][field] for sid in off}
        if shown and field in declared:
            applied.append({"field": field, "reason": declared[field], "by_system": shown})
        elif shown:
            c["findings"].append({"code": code, "field": field, "by_system": shown})
    fit = checks["preprocessing"]
    for sid, s in by_id.items():
        t = s.get("transforms")
        if t is None:
            fit["missing_evidence"].append({"system": sid, "missing": "transforms"})
            continue
        found = k["szl_dataset_audit_leakage"](rows or [], "split", {
            "schema": "szl.dataset-leakage/v1", "row_id_column": "id", "transforms": t,
            "preprocessing_training_splits": train,
            "expected_transform_ids": [x["id"] for x in t if type(x) is dict and "id" in x] if type(t) is list else []
        })[0]["preprocessing"]
        fit["findings"] += [dict(f, system=sid) for f in found["findings"]]
        fit["missing_evidence"] += [dict(m, system=sid) if type(m) is dict else {"system": sid, "missing": m}
                                    for m in found["missing_evidence"]]
    for c in checks.values():
        c["status"] = "PARITY_ISSUES" if c["findings"] else "UNKNOWN" if c["missing_evidence"] else "PARITY_OK"
    states = {c["status"] for c in checks.values()}
    return {"schema": "szl.benchmark-parity-report/v1",
            "status": next(s for s in ("PARITY_ISSUES", "UNKNOWN", "PARITY_OK") if s in states),
            "issues": sorted({f["code"] for c in checks.values() for f in c["findings"]}),
            "checks": checks, "declared_asymmetries": applied, "spec_sha256": hashlib.sha256(blob).hexdigest(),
            "readiness": "REVIEW", "scientific_suitability": "NOT_ASSESSED", "behavioral_evaluation": "NOT_MEASURED",
            "limitations": ["Declared digests, budgets and fit manifests only; no system is rerun or rescored"]}
