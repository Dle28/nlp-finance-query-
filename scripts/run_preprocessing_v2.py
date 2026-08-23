#!/usr/bin/env python3
"""Build a fresh, hash-bound ViFinQA preprocessing V2 review checkout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.preprocessing_v2 import run_preprocessing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = run_preprocessing(args.config, args.output_dir)
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "manifest": str(result.manifest_path),
                "table_count": result.table_count,
                "status_counts": result.status_counts,
                "current_benchmark_label_count": result.current_benchmark_label_count,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
