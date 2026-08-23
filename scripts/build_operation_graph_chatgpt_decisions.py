#!/usr/bin/env python3
"""Build fail-closed ChatGPT reviews for operation-graph candidates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.operation_graph_chatgpt_reviews import build_decisions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-queue", type=Path, required=True)
    parser.add_argument("--review-manifest", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--review-spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_decisions(**vars(args)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
