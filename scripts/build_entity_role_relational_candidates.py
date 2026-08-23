#!/usr/bin/env python3
"""Build residual exact-source issuer/subsidiary role candidates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.entity_role_relational_reviews import build_relational_candidate_queue


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-candidate-queue", type=Path, required=True)
    parser.add_argument("--prior-decisions", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_relational_candidate_queue(**vars(args)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
