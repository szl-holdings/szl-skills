#!/usr/bin/env python3
"""Run a bounded range audit and retain a receipt for exact input bytes."""

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kernel import REPORT_SCHEMA, audit  # noqa: E402


def _duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_KEY")
        result[key] = value
    return result


def _numeric_token(token):
    from kernel import NUMBER  # noqa: E402
    if len(token) > 32 or NUMBER.fullmatch(token) is None:
        raise ValueError("BAD_NUMBER")
    return Decimal(token)


def _integer_token(token):
    if len(token) > 16 or token.startswith("+"):
        raise ValueError("BAD_NUMBER")
    if token.startswith("-"):
        digits = token[1:]
    else:
        digits = token
    if not digits or len(digits) > 15 or (len(digits) > 1 and digits[0] == "0"):
        raise ValueError("BAD_NUMBER")
    return int(token)


def _constant(_token):
    raise ValueError("BAD_NUMBER")


def _load(raw):
    if len(raw) > 1_000_000:
        raise ValueError("INPUT_TOO_LARGE")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicate_pairs,
                          parse_float=_numeric_token, parse_int=_integer_token,
                          parse_constant=_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("MALFORMED_JSON") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    raw = None
    try:
        with args.input.open("rb") as source:
            raw = source.read(1_000_001)
        report = audit(_load(raw))
    except ValueError as exc:
        report = {"schema": REPORT_SCHEMA, "status": "INVALID_INPUT", "error_code": str(exc),
                  "scientific_truth_verified": False,
                  "probabilistic_coverage_established": False}
    except OSError:
        report = {"schema": REPORT_SCHEMA, "status": "INVALID_INPUT", "error_code": "INPUT_IO",
                  "scientific_truth_verified": False,
                  "probabilistic_coverage_established": False}
    report["input_bytes_sha256"] = hashlib.sha256(raw).hexdigest() if raw is not None else None
    rendered = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        try:
            with args.output.open("x", encoding="utf-8", newline="\n") as destination:
                destination.write(rendered)
        except FileExistsError:
            print("OUTPUT_EXISTS", file=sys.stderr)
            return 2
        except OSError:
            print("OUTPUT_IO", file=sys.stderr)
            return 2
    return {"PASS_DECLARED_BOUNDS": 0, "FAIL_DECLARED_BOUNDS": 1,
            "UNKNOWN": 1, "INVALID_INPUT": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
