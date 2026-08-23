#!/usr/bin/env python3
"""Apply approved operation literals to the graph-review gate only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.route_context_promotions import build_promotion


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-queue", type=Path, required=True)
    parser.add_argument("--operation-manifest", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--decision-manifest", type=Path, required=True)
    parser.add_argument("--promotion-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_promotion(**vars(args)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
