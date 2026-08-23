#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.route_context_repairs import build_route_context_repair_queue


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation-queue", type=Path, required=True)
    parser.add_argument("--operation-manifest", type=Path, required=True)
    parser.add_argument("--route-packets", type=Path, required=True)
    parser.add_argument("--route-packets-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_route_context_repair_queue(**vars(args))
    print(json.dumps({"counts": result["counts"], "manifest_path": result["manifest_path"]}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

