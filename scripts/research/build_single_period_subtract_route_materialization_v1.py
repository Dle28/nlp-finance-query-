#!/usr/bin/env python3
"""Build the source-gated single-period subtraction E2E overlay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.single_period_subtract_route_materialization import (
    build_single_period_subtract_route_materialization,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in (
        "compiler-reclassification-dir", "source-audit-dir", "base-period-packets",
        "base-period-manifest", "base-route-overlay", "base-route-overlay-manifest",
        "full-corpus-artifact-dir", "row-review-queue", "structured-tables",
        "evidence-context", "evidence-context-manifest", "output-dir",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    parser.add_argument("--minimum-row-jaccard", type=float, default=0.9)
    parser.add_argument("--minimum-row-margin", type=float, default=0.2)
    args = parser.parse_args()
    result = build_single_period_subtract_route_materialization(
        compiler_reclassification_dir=args.compiler_reclassification_dir,
        source_audit_dir=args.source_audit_dir,
        base_period_packets_path=args.base_period_packets,
        base_period_manifest_path=args.base_period_manifest,
        base_route_overlay_path=args.base_route_overlay,
        base_route_overlay_manifest_path=args.base_route_overlay_manifest,
        full_corpus_artifact_dir=args.full_corpus_artifact_dir,
        row_review_queue_path=args.row_review_queue,
        structured_tables_path=args.structured_tables,
        evidence_context_path=args.evidence_context,
        evidence_context_manifest_path=args.evidence_context_manifest,
        output_dir=args.output_dir,
        expected_question_count=args.expected_question_count,
        minimum_row_jaccard=args.minimum_row_jaccard,
        minimum_row_margin=args.minimum_row_margin,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
