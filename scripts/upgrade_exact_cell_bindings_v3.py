#!/usr/bin/env python3
"""Upgrade V2 exact-cell bindings with context-derived unit evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.exact_cell_bindings_v3 import upgrade_context_unit_bindings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bindings-v2", type=Path, required=True)
    parser.add_argument("--bindings-v2-manifest", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, required=True)
    parser.add_argument("--evidence-context-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(upgrade_context_unit_bindings(**vars(args)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
