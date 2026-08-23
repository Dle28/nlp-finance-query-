#!/usr/bin/env python3
"""Build fail-closed ChatGPT decisions for navigation evidence packets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.navigation_chatgpt_reviews import build_decisions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-packets", type=Path, required=True)
    parser.add_argument("--evidence-manifest", type=Path, required=True)
    parser.add_argument("--review-spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_decisions(**vars(args)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
