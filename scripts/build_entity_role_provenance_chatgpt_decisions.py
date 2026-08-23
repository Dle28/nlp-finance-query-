#!/usr/bin/env python3
"""Build validated ChatGPT entity-role provenance decisions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.entity_role_provenance_reviews import build_decisions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-queue", type=Path, required=True)
    parser.add_argument("--review-spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_decisions(**vars(args)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
