#!/usr/bin/env python3
"""Keyless public OSF witness CLI. No provider mutation or token handling."""

import argparse
import importlib.util
import json
import pathlib
import stat
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("osf_registration_witness", ROOT / "kernel.py")
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=pathlib.Path, help="Bounded witness config JSON")
    args = parser.parse_args(argv)
    try:
        if not stat.S_ISREG(args.config.stat().st_mode) or args.config.stat().st_size > 8192:
            raise ValueError("Config must be a regular file of at most 8 KiB")
        with args.config.open("rb") as source:
            raw = source.read(8193)
        if len(raw) > 8192:
            raise ValueError("Config exceeds 8 KiB")
        config = json.loads(raw.decode("utf-8"), object_pairs_hook=kernel._unique_pairs,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        result = kernel.verify(config)
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        result = {"schema": "szl.osf-registration-witness-report.v1", "status": "INVALID_INPUT",
                  "findings": ["INVALID_CONFIG"], "provider_write_performed": False,
                  "pre_data_timing": "NOT_VERIFIED", "scientific_validity": "NOT_EVALUATED",
                  "detail": str(error)[:200]}
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return {"PUBLIC_PLAN_BYTES_MATCHED": 0, "PUBLIC_FILE_BYTES_MATCHED": 0,
            "BLOCKED": 1, "UNAVAILABLE": 2, "INVALID_INPUT": 3}[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
