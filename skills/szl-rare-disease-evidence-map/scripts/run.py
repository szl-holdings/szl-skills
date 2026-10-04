#!/usr/bin/env python3
"""Print a bounded, synthetic-only HPO/ClinVar-shaped evidence map."""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kernel import EvidenceMapError, map_evidence, read_input


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="synthetic manifest with exact source hashes")
    parser.add_argument("hpo", help="synthetic HPO-shaped JSON source")
    parser.add_argument("clinvar", help="synthetic ClinVar-shaped JSON source")
    args = parser.parse_args(argv)
    try:
        manifest = read_input(args.manifest)
        hpo = read_input(args.hpo)
        clinvar = read_input(args.clinvar)
        result = map_evidence(manifest, hpo, clinvar)
    except (OSError, EvidenceMapError) as error:
        print("evidence map: HOLD: " + str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
