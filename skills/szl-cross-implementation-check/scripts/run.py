#!/usr/bin/env python3
"""Usage: python scripts/run.py COMPARISON.json [--output REPORT.json]
Exit 0 = comparison ran (read "status"), 2 = ERROR (also duplicate JSON keys, NaN, Infinity and,
in stability mode, float literals that differ from their shortest repr)."""
import argparse, json, pathlib, sys
from decimal import Decimal
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kernel import STABILITY_REPORT, STABILITY_SCHEMA, szl_cross_implementation_check  # noqa: E402

def pairs(items):
    if len({k for k, _ in items}) < len(items): raise ValueError("duplicate JSON key")
    return dict(items)

def constant(name): raise ValueError("non-finite JSON number " + name)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("comparison")
    ap.add_argument("--output", default=None)
    a = ap.parse_args()
    lossy = []
    def number(text):
        try: kept = Decimal(text) == Decimal(repr(float(text)))
        except ArithmeticError: kept = False
        if not kept: lossy.append(text)
        return float(text)
    try:
        doc = json.loads(pathlib.Path(a.comparison).read_text(encoding="utf-8"), object_pairs_hook=pairs,
                         parse_constant=constant, parse_float=number)
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "reason": f"cannot read comparison: {exc}"})); return 2
    if lossy and isinstance(doc, dict) and doc.get("schema") == STABILITY_SCHEMA:
        report = {"schema": STABILITY_REPORT, "status": "ERROR", "reason": "float literals differ from their"
                  " shortest binary64 repr; write them as decimal strings (<= 400 digits each side of the point,"
                  " exponent <= 3 digits)", "keys": lossy}
    else:
        report = szl_cross_implementation_check(doc)
    text = json.dumps(report, indent=2)
    if a.output:
        pathlib.Path(a.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 2 if report["status"] == "ERROR" else 0

if __name__ == "__main__":
    sys.exit(main())
