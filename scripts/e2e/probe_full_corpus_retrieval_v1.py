#!/usr/bin/env python3
"""Run value-blind lexical or dense retrieval probes against a completed index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.e2e.core.dense_retrieval import search_dense  # noqa: E402
from finance_query.e2e.core.retrieval_probes import build_retrieval_probe_artifact  # noqa: E402
from finance_query.e2e.core.table_retrieval import search_lexical  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("lexical", "dense"), required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--probes", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--match-mode", choices=("all", "any"), default="all")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    if args.kind == "lexical":
        def search(query: str, ticker: str, report_year: int, scope: str | None, limit: int):
            return search_lexical(
                index_path=args.index,
                query=query,
                ticker=ticker,
                report_year=report_year,
                scope=scope,
                limit=limit,
                match_mode=args.match_mode,
            )
    else:
        def search(query: str, ticker: str, report_year: int, scope: str | None, limit: int):
            return search_dense(
                index_dir=args.index,
                query=query,
                ticker=ticker,
                report_year=report_year,
                scope=scope,
                limit=limit,
                requested_device=args.device,
            )

    print(
        json.dumps(
            build_retrieval_probe_artifact(
                probe_path=args.probes,
                output_dir=args.output_dir,
                retrieval_kind=args.kind,
                top_k=args.top_k,
                search=search,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
