#!/usr/bin/env python3
"""Build scope-bound ChatGPT decisions for cross-entity operand packets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.cross_entity_operand_reviews import build_chatgpt_decisions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--packet-manifest", type=Path, required=True)
    parser.add_argument("--review-spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_chatgpt_decisions(**vars(args)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
