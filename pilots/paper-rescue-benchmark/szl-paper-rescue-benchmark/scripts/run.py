#!/usr/bin/env python3
"""Offline, bounded PDF evidence-record comparison; no extraction or OCR."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kernel import (RECORD_SCHEMA, REPORT_SCHEMA, exact_keys, score,
                    validate_pipeline, validate_rights)  # noqa: E402


def duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def parse_integer(token):
    if len(token) > 16:
        raise ValueError("INTEGER_LIMIT")
    return int(token)


def reject_constant(_token):
    raise ValueError("NONFINITE_NUMBER")


def load_json(path):
    with path.open("rb") as source:
        raw = source.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("JSON_SIZE_LIMIT")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=duplicate_pairs,
                           parse_int=parse_integer, parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("MALFORMED_JSON") from exc
    return value, hashlib.sha256(raw).hexdigest()


def pdf_digest(path):
    total = 0
    hashed = hashlib.sha256()
    with path.open("rb") as source:
        header = source.read(5)
        if header != b"%PDF-":
            raise ValueError("NOT_A_PDF_HEADER")
        hashed.update(header)
        total += len(header)
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 128 * 1024 * 1024:
                raise ValueError("PDF_SIZE_LIMIT")
            hashed.update(chunk)
    return hashed.hexdigest()


def write_exclusive(path, rendered):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         prefix=f".{path.name}.", suffix=".tmp",
                                         dir=path.parent, delete=False) as destination:
            temporary = Path(destination.name)
            destination.write(rendered)
            destination.flush()
            os.fsync(destination.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--records", required=True, type=Path)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        records, records_sha256 = load_json(args.records)
        # Require the operator's rights/privacy attestation before opening the PDF.
        exact_keys(records, ("schema", "pdf_sha256", "declared_pages", "rights",
                             "pipeline", "items"))
        if records["schema"] != RECORD_SCHEMA:
            raise ValueError("BAD_RECORD_SCHEMA")
        validate_rights(records["rights"])
        validate_pipeline(records["pipeline"])
        pdf_sha256 = pdf_digest(args.pdf)
        gold, gold_sha256 = load_json(args.gold)
        report = score(records, gold, pdf_sha256)
        report["records_bytes_sha256"] = records_sha256
        report["gold_bytes_sha256"] = gold_sha256
        exit_code = 1 if report["status"] == "REFERENCE_MISMATCH" else 0
    except ValueError as exc:
        report = {"schema": REPORT_SCHEMA, "status": "INVALID_INPUT",
                  "error_code": str(exc), "scientific_truth_verified": False,
                  "visual_truth_verified": False, "clinical_use_qualified": False,
                  "rights_verified": False, "human_adjudication_verified": False,
                  "pipeline_remote_services_verified": False,
                  "actual_pdf_page_count_verified": False}
        exit_code = 2
    except OSError:
        report = {"schema": REPORT_SCHEMA, "status": "INVALID_INPUT",
                  "error_code": "INPUT_IO", "scientific_truth_verified": False,
                  "visual_truth_verified": False, "clinical_use_qualified": False,
                  "rights_verified": False, "human_adjudication_verified": False,
                  "pipeline_remote_services_verified": False,
                  "actual_pdf_page_count_verified": False}
        exit_code = 2
    rendered = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        try:
            write_exclusive(args.output, rendered)
        except OSError:
            sys.stderr.write("OUTPUT_IO: exclusive report publication failed\n")
            return 2
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
