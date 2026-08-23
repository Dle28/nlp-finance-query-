#!/usr/bin/env python3
"""Build a hash-bound review queue for missing entity-role provenance."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.entity_role_provenance import build_candidate_queue


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-briefs", type=Path, required=True)
    parser.add_argument("--campaign-bundle", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_candidate_queue(**vars(args)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
