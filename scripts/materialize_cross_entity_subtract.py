#!/usr/bin/env python3
"""Privately reopen approved operand cells and replay Decimal subtraction."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.cross_entity_operand_reviews import materialize_and_execute


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--packet-manifest", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--decision-manifest", type=Path, required=True)
    parser.add_argument("--normalized-tables", type=Path, required=True)
    parser.add_argument("--preprocessing-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(materialize_and_execute(**vars(args)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
