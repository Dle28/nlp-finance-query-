#!/usr/bin/env python3
"""Create a fresh Phase 0-2 Certified Canonical Layer research checkout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical import run_certified_canonical


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = run_certified_canonical(args.config, args.output_dir)
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "manifest": str(result.manifest_path),
                "table_count": result.table_count,
                "lifecycle_counts": result.lifecycle_counts,
                "benchmark_packet_count": result.benchmark_packet_count,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
