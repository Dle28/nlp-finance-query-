#!/usr/bin/env python3
"""Merge reviewed cross-entity promotions against one common base binding set."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.cross_entity_binding_promotions import (
    merge_cross_entity_binding_promotion_reviews,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, action="append", required=True)
    parser.add_argument("--base-bindings", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    kwargs = vars(args)
    kwargs["source_manifests"] = kwargs.pop("source_manifest")
    print(json.dumps(merge_cross_entity_binding_promotion_reviews(**kwargs), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
