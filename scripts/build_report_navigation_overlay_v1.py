#!/usr/bin/env python3
"""Build a full-coverage, non-promotable contents-page guard from V10 tables."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finance_query.report_navigation_overlay import build_report_navigation_overlay  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-tables", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_report_navigation_overlay(raw_tables=args.raw_tables, output_dir=args.output_dir)
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "table_count": result.table_count,
                "contents_page_count": result.contents_page_count,
                "training_eligible": False,
                "certification_allowed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
