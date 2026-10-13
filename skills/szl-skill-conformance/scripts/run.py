#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate retained evidence only; never run the candidate skill."""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import kernel

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", required=True)
parser.add_argument("--host-export")
parser.add_argument("--trials")
parser.add_argument("--trial-root")
parser.add_argument("--imported-name", help="Explicit host display-name alias; payload bytes must remain identical")
parser.add_argument("--legal-root", help="Source-revision root whose LICENSE and NOTICE staging adds to --source")
args = parser.parse_args()
report = kernel.szl_check_skill_conformance(args.source, args.host_export, args.trials, args.trial_root, args.imported_name, args.legal_root)
print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
sys.exit({"CONSISTENT_SUPPLIED_EVIDENCE": 0, "INCOMPLETE": 2, "REJECTED": 1}[report["offline_conformance"]])
