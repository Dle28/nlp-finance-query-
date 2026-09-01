#!/usr/bin/env python3
"""Build source-bound diagnostics from strict full-corpus row candidates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.full_corpus_direct_lookup_adapter import build_full_corpus_direct_lookup_adapter


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in (
        "triage",
        "plans",
        "base-route-overlay",
        "row-review-queue",
        "full-corpus-manifest",
        "structured-tables",
        "evidence-context",
        "evidence-context-manifest",
        "output-dir",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    parser.add_argument("--minimum-row-jaccard", type=float, default=0.9)
    args = parser.parse_args()
    result = build_full_corpus_direct_lookup_adapter(
        triage_path=args.triage,
        plans_path=args.plans,
        base_route_overlay_path=args.base_route_overlay,
        row_review_queue_path=args.row_review_queue,
        full_corpus_manifest_path=args.full_corpus_manifest,
        structured_tables_path=args.structured_tables,
        evidence_context_path=args.evidence_context,
        evidence_context_manifest_path=args.evidence_context_manifest,
        output_dir=args.output_dir,
        expected_question_count=args.expected_question_count,
        minimum_row_jaccard=args.minimum_row_jaccard,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
