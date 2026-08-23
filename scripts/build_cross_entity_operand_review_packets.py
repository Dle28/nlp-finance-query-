#!/usr/bin/env python3
"""Build numeric-value-free exact-source packets for cross-entity review."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.cross_entity_operand_reviews import build_review_packets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-path", type=Path, required=True)
    parser.add_argument("--normalized-tables", type=Path, required=True)
    parser.add_argument("--preprocessing-manifest", type=Path, required=True)
    parser.add_argument("--graph-decisions", type=Path, required=True)
    parser.add_argument("--graph-manifest", type=Path, required=True)
    parser.add_argument("--typed-plans", type=Path, required=True)
    parser.add_argument("--typed-plan-manifest", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_review_packets(**vars(args)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
