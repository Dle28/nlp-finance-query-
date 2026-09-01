#!/usr/bin/env python3
"""Validate a section RAG evaluation artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.section_rag_evaluation import validate_section_hierarchical_rag


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--expected-route-count", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(validate_section_hierarchical_rag(args.artifact_dir, expected_route_count=args.expected_route_count), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
