#!/usr/bin/env python3
"""Build hash-bound, numeric-literal-free navigation review evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.navigation_review_evidence import build_evidence_packets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--queue-manifest", type=Path, required=True)
    parser.add_argument("--route-packets", type=Path, required=True)
    parser.add_argument("--route-manifest", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--table-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_evidence_packets(**vars(args)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
