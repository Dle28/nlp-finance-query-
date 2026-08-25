#!/usr/bin/env python3
"""Build a fail-closed whole-question operation-graph review queue."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.e2e.operation_graph_review import build_operation_graph_queue


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route-overlay", type=Path, required=True)
    parser.add_argument("--route-overlay-manifest", type=Path, required=True)
    parser.add_argument("--route-packets", type=Path, required=True)
    parser.add_argument("--route-packets-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_operation_graph_queue(**vars(args))
    print(
        json.dumps(
            {
                "status": result["status"],
                "counts": result["counts"],
                "manifest_path": result["manifest_path"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
