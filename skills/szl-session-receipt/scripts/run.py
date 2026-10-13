#!/usr/bin/env python3
"""Usage:
  python scripts/run.py record MANIFEST.json --root PROJECT_DIR [--output receipt.json] [--sign-key KEY.pem] [--max-file-bytes N]
  python scripts/run.py verify receipt.json --root PROJECT_DIR [--scan] [--max-file-bytes N]
  python scripts/run.py methods receipt.json          (prints the Methods paragraph only)

record/verify exit 0 = ran (read "status"), 2 = ERROR (also duplicate JSON keys, NaN/Infinity, 1e400).
--scan: EXTRA_FILES_PRESENT for unrecorded entries in the root and directories leading to recorded files.
--sign-key is optional and needs `pip install szl-receipt-dsse`; without it the receipt is UNSIGNED and says so."""
import argparse, json, math, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kernel import MAX_FILE_BYTES, szl_make_session_receipt, szl_sign_session_receipt, szl_verify_session_receipt  # noqa: E402

def _pairs(pairs):
    if len({k for k, _ in pairs}) != len(pairs):
        raise ValueError("duplicate JSON key")
    return dict(pairs)

def _constant(token):
    raise ValueError("non-finite JSON number " + token)

def _float(token):
    return float(token) if math.isfinite(float(token)) else _constant(token)

def read(p):
    return json.loads(pathlib.Path(p).read_text(encoding="utf-8"), object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float)

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record"); r.add_argument("manifest"); r.add_argument("--root", required=True); r.add_argument("--output", default=None); r.add_argument("--sign-key", default=None)
    v = sub.add_parser("verify"); v.add_argument("receipt"); v.add_argument("--root", required=True); v.add_argument("--scan", action="store_true")
    for p in (r, v):
        p.add_argument("--max-file-bytes", type=int, default=MAX_FILE_BYTES)
    m = sub.add_parser("methods"); m.add_argument("receipt")
    a = ap.parse_args()
    try:
        doc = read(a.manifest if a.cmd == "record" else a.receipt)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "ERROR", "reason": "input is not readable strict JSON: %s" % exc}, indent=2)); return 2
    if a.cmd == "record":
        receipt = szl_make_session_receipt(a.root, doc, a.max_file_bytes)
        if receipt["status"] == "ERROR":
            print(json.dumps(receipt, indent=2)); return 2
        if a.sign_key:
            receipt = szl_sign_session_receipt(receipt, pathlib.Path(a.sign_key).read_bytes())
        text = json.dumps(receipt, indent=2)
        if a.output:
            pathlib.Path(a.output).write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0
    if a.cmd == "verify":
        result = szl_verify_session_receipt(a.root, doc, scan=a.scan, max_file_bytes=a.max_file_bytes)
        print(json.dumps(result, indent=2))
        return 2 if result["status"] == "ERROR" else 0
    print(doc.get("methods_paragraph", "") if isinstance(doc, dict) else "")
    return 0

if __name__ == "__main__":
    sys.exit(main())
