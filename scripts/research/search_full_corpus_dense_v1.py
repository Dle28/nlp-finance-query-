#!/usr/bin/env python3
"""Search dense navigation candidates with explicit ticker/year filters."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.e2e.core.dense_retrieval import search_dense  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-dir", type=Path, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--scope")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="cpu")
    args = parser.parse_args()
    rows = search_dense(
        index_dir=args.index_dir,
        query=args.query,
        ticker=args.ticker,
        report_year=args.year,
        scope=args.scope,
        limit=args.limit,
        requested_device=args.device,
    )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
