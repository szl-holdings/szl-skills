"""szl-cross-implementation-check — do two independent implementations agree?

Stdlib only. Offline. Compares two result sets (for example an R script and a
Python notebook that claim to compute the same statistics) and returns one of
three verdicts per quantity and overall, using the vocabulary of the published
szl-crosscheck package:

  CONSISTENT    both report the quantity and |a-b| <= max(atol, rtol*max(|a|,|b|))
  DIVERGENT     both report it and the difference exceeds the declared tolerance
  INCOMPARABLE  only one side reports it, a value is non-finite or non-numeric,
                or the two sides declare different inputs (input digests differ)

Overall: DIVERGENT if any compared quantity diverges; else INCOMPARABLE if any
quantity is incomparable, the inputs differ or only one side declares a digest;
else CONSISTENT. Agreement is evidence that two implementations agree on these
inputs; it is not evidence that either is correct. Stability mode: see
references/contract.md.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from decimal import Context, Inexact
from fractions import Fraction

SCHEMA = "szl.cross-implementation-report.v1"
STABILITY_SCHEMA = "szl.cross-implementation.stability.v1"
STABILITY_REPORT = "szl.cross-implementation-stability-report.v1"
AXES = ("precision", "threads", "summation_order", "seed")
DECIMAL = re.compile(r"-?[0-9]{1,40}(\.[0-9]{1,40})?([eE][+-]?[0-9]{1,3})?")
HEX = re.compile(r"[0-9a-f]{64}")


def szl_canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        f = float(value)
    except OverflowError:
        return None
    return f if math.isfinite(f) else None


def _tol(value):
    f = _number(value)
    return None if f is None or f < 0 else f


def _flatten(prefix, value, out, clash):
    if isinstance(value, dict):
        for k in sorted(value):
            _flatten(f"{prefix}.{k}" if prefix else str(k), value[k], out, clash)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out, clash)
    else:
        if prefix in out:
            clash.append(prefix)
        out[prefix] = value
    return out


def _side(label, side):
    if not isinstance(side, dict) or not isinstance(side.get("results"), dict):
        raise ValueError(f"{label}: must be an object with a results object")
    clash = []
    return {
        "label": side.get("label", label),
        "implementation": side.get("implementation"),
        "input_sha256": side.get("input_sha256"),
        "flat": _flatten("", side["results"], {}, clash),
        "clash": [f"{label}: {k}" for k in clash],
    }


def _error(schema, reason, keys):
    return {"schema": schema, "status": "ERROR", "reason": reason, "keys": keys}


def szl_compare_quantity(name, a, b, atol, rtol) -> dict:
    row = {"quantity": name, "a": a, "b": b, "status": None, "abs_diff": None, "tolerance": None}
    if a is None or b is None:
        row.update(status="INCOMPARABLE", reason="NULL_ON_BOTH_SIDES" if a is b else "REPORTED_BY_ONE_SIDE_ONLY")
        return row
    fa, fb = _number(a), _number(b)
    if fa is None or fb is None:
        if isinstance(a, str) and isinstance(b, str) or isinstance(a, bool) and isinstance(b, bool):
            row.update(status="CONSISTENT" if a == b else "DIVERGENT", reason="EXACT_COMPARISON")
            return row
        row.update(status="INCOMPARABLE", reason="NON_NUMERIC_OR_NON_FINITE")
        return row
    tol = max(atol, rtol * max(abs(fa), abs(fb)))
    diff = abs(fa - fb)
    row.update(abs_diff=diff, tolerance=tol, status="CONSISTENT" if diff <= tol else "DIVERGENT",
               reason="WITHIN_TOLERANCE" if diff <= tol else "EXCEEDS_TOLERANCE")
    return row


def szl_cross_implementation_check(document) -> dict:
    if not isinstance(document, dict):
        return {"schema": SCHEMA, "status": "ERROR", "reason": "document must be an object"}
    if document.get("schema") == STABILITY_SCHEMA:
        return szl_stability_check(document)
    try:
        a = _side("a", document.get("a"))
        b = _side("b", document.get("b"))
    except ValueError as exc:
        return {"schema": SCHEMA, "status": "ERROR", "reason": str(exc)}
    if a["clash"] or b["clash"]:
        return _error(SCHEMA, "FLATTEN_COLLISION", a["clash"] + b["clash"])
    tol = document.get("tolerance", {}) or {}
    atol = _tol(tol.get("atol", 0.0)) if isinstance(tol, dict) else None
    rtol = _tol(tol.get("rtol", 0.0)) if isinstance(tol, dict) else None
    if atol is None or rtol is None:
        return {"schema": SCHEMA, "status": "ERROR", "reason": "tolerances must be finite and non-negative"}
    overrides = document.get("per_quantity_tolerance", {}) or {}
    if not isinstance(overrides, dict):
        return _error(SCHEMA, "INVALID_OVERRIDE", [])
    bad = sorted(k for k, o in overrides.items() if not isinstance(o, dict) or not set(o) <= {"atol", "rtol"}
                 or None in map(_tol, o.values()))
    names = sorted(set(a["flat"]) | set(b["flat"]))
    unknown = sorted(set(overrides) - set(names))
    if bad or unknown:
        return _error(SCHEMA, "INVALID_OVERRIDE" if bad else "UNKNOWN_OVERRIDE_KEY", bad or unknown)
    rows = []
    for name in names:
        o = overrides.get(name, {})
        rows.append(szl_compare_quantity(name, a["flat"].get(name), b["flat"].get(name),
                                         float(o.get("atol", atol)), float(o.get("rtol", rtol))))
    one_sided = bool(a["input_sha256"]) != bool(b["input_sha256"])
    inputs_match = None
    if a["input_sha256"] and b["input_sha256"]:
        inputs_match = a["input_sha256"] == b["input_sha256"]
    statuses = [r["status"] for r in rows]
    if "DIVERGENT" in statuses:
        status = "DIVERGENT"
    elif "INCOMPARABLE" in statuses or inputs_match is False or not rows or one_sided:
        status = "INCOMPARABLE"
    else:
        status = "CONSISTENT"
    return {
        "schema": SCHEMA,
        "status": status,
        **({"reason": "INPUT_BINDING_INCOMPLETE"} if one_sided else {}),
        "inputs_match": inputs_match,
        "implementations": [a["implementation"], b["implementation"]],
        "compared": sum(1 for s in statuses if s != "INCOMPARABLE"),
        "consistent": statuses.count("CONSISTENT"),
        "divergent": statuses.count("DIVERGENT"),
        "incomparable": statuses.count("INCOMPARABLE"),
        "tolerance": {"atol": atol, "rtol": rtol},
        "quantities": rows,
        "document_sha256": hashlib.sha256(szl_canonical(document).encode()).hexdigest(),
        "scope": "Agreement between two supplied result sets on declared inputs; correctness of either implementation is not established.",
    }


def szl_exact_value(value):
    """Fraction of a decimal string or of a finite number's shortest repr; else None."""
    if isinstance(value, str):
        return Fraction(value) if DECIMAL.fullmatch(value) else None
    f = _number(value)
    return None if f is None else Fraction(value if isinstance(value, int) else repr(f))


def szl_exact_text(q) -> str:
    """Exact decimal text of a Fraction with a 2^a*5^b denominator; Inexact is trapped."""
    n, d = q.numerator, q.denominator
    return str(Context(prec=abs(n).bit_length() // 3 + d.bit_length() + 2, traps=[Inexact]).divide(n, d))


def szl_stability_quantity(name, values, required) -> dict:
    qs = sorted(q for q in map(szl_exact_value, values) if q is not None)
    if len(qs) < len(values):
        return dict(quantity=name, status="UNSTABLE", reason="NOT_NUMERIC_IN_EVERY_RUN")
    lo, hi, n = qs[0], qs[-1], len(qs)
    median, scale = (qs[(n - 1) // 2] + qs[n // 2]) / 2, max(abs(lo), abs(hi))
    rel = (hi - lo) / scale if scale else Fraction(0)
    digits = None
    if rel:
        digits = 0
        while rel * 10 ** (digits + 1) <= 1:
            digits += 1
    ok = digits is None or digits >= required
    text = [szl_exact_text(q) for q in (lo, hi, median)]
    return dict(quantity=name, status="STABLE" if ok else "UNSTABLE",
                reason="WITHIN_REQUIRED_DIGITS" if ok else "BELOW_REQUIRED_DIGITS",
                min=text[0], max=text[1], median=text[2], relative_spread=float(rel), agreed_digits=digits,
                sign_consistent=len({(q > 0) - (q < 0) for q in qs}) == 1,
                cancellation=bool(scale) and abs(median) * 10 <= scale)


def szl_stability_check(document) -> dict:
    runs, req = document.get("runs"), document.get("required_digits")
    if not isinstance(runs, list) or len(runs) < 3 or not all(
            isinstance(r, dict) and isinstance(r.get("results"), dict) for r in runs):
        return _error(STABILITY_REPORT, "runs: need >= 3 objects with a results object", [])
    perts = [r.get("perturbation") for r in runs]
    text = [{k: str(v) for k, v in p.items()} if isinstance(p, dict) else None for p in perts]
    bad = [f"runs[{i}]" for i, p in enumerate(perts) if not (isinstance(p, dict) and p and set(p) <= set(AXES)
           and all(type(v) is int or type(v) is str and v == v.strip() != "" for v in p.values())
           and text.index(text[i]) == i)]
    if bad or type(req) is not int or req < 1:
        return _error(STABILITY_REPORT, "perturbations: non-empty, distinct as text, keys from precision/threads/"
                      "summation_order/seed, int or trimmed str values; required_digits: int >= 1", bad)
    flats, clash = [], []
    for i, r in enumerate(runs):
        c = []
        flats.append(_flatten("", r["results"], {}, c))
        clash += [f"runs[{i}]: {k}" for k in c]
    if clash:
        return _error(STABILITY_REPORT, "FLATTEN_COLLISION", clash)
    rows = [szl_stability_quantity(n, [f.get(n) for f in flats], req) for n in sorted(set().union(*flats))]
    unstable = [r["quantity"] for r in rows if r["status"] == "UNSTABLE"]
    digests = [d for d in (r.get("input_sha256") for r in runs) if isinstance(d, str) and HEX.fullmatch(d)]
    complete = len(digests) == len(runs)
    match = False if len(set(digests)) > 1 else complete or None
    return dict(schema=STABILITY_REPORT, status="UNSTABLE" if unstable else "STABLE" if match and rows else "INCOMPARABLE",
                **({} if complete else {"reason": "INPUT_BINDING_INCOMPLETE"}), inputs_match=match, runs=len(runs),
                required_digits=req, perturbed_axes=[k for k in AXES if len({t.get(k) for t in text}) > 1],
                issues=unstable, quantities=rows, document_sha256=hashlib.sha256(szl_canonical(document).encode()).hexdigest(),
                scope="One computation across declared perturbations of one input; accuracy is not established.")
