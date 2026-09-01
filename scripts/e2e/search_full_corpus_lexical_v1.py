#!/usr/bin/env python3
"""Search the validated lexical navigation index with explicit metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.e2e.core.table_retrieval import search_lexical  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--scope", choices=("consolidated", "separate", "aggregated", "unknown"))
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    rows = search_lexical(
        index_path=args.index,
        query=args.query,
        ticker=args.ticker,
        report_year=args.year,
        scope=args.scope,
        limit=args.limit,
    )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
