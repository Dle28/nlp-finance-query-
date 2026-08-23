#!/usr/bin/env python3
"""Build a value-free ChatGPT promotion receipt for cross-entity bindings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.cross_entity_binding_promotions import (
    build_cross_entity_binding_promotion_review,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operand-packets", type=Path, required=True)
    parser.add_argument("--operand-packet-manifest", type=Path, required=True)
    parser.add_argument("--operand-decisions", type=Path, required=True)
    parser.add_argument("--operand-decision-manifest", type=Path, required=True)
    parser.add_argument("--base-bindings", type=Path, required=True)
    parser.add_argument("--base-bindings-manifest", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, required=True)
    parser.add_argument("--evidence-context-manifest", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--config-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_cross_entity_binding_promotion_review(**vars(args)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
