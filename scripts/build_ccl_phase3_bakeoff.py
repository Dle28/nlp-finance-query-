#!/usr/bin/env python3
"""Build a hash-bound CCL Phase 3 model bake-off job; never run a model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical import build_bakeoff_job


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = build_bakeoff_job(args.config, args.output_dir)
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "manifest": str(result.manifest_path),
                "request_count": result.request_count,
                "route_counts": result.route_counts,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
