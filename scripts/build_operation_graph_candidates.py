#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.operation_graph_candidates import build_operation_graph_candidates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-queue", type=Path, required=True)
    parser.add_argument("--review-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_operation_graph_candidates(**vars(args))
    print(json.dumps({"counts": result["counts"], "manifest_path": result["manifest_path"]}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

