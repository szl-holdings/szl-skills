#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check a supplied skill-use record without executing skills or authenticating a host."""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import kernel

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", required=True, help="Exact local skill directory")
parser.add_argument("--record", required=True, help="Use-record JSON")
parser.add_argument("--retained", required=True, help="Root for artifact and optional trace files")
args = parser.parse_args()
report = kernel.check_use_record(args.source, args.record, args.retained)
print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
sys.exit({"CONSISTENT_SUPPLIED_RECORD": 0, "INCOMPLETE_HOST_TRACE": 2,
          "REJECTED": 1}[report["record_consistency"]])
