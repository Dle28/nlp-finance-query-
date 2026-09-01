#!/usr/bin/env python3
"""Build the full-population feedback breakthrough ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.population_feedback_breakthrough import (  # noqa: E402
    build_population_feedback_breakthrough,
)


DEFAULT_RESEARCH_DOCS = (
    ROOT / "docs/research/CANDIDATE_PLAN_SELECTOR_REVIEW_V1.md",
    ROOT / "docs/research/RESEARCH_INTEGRATION_LOG_V1.md",
    ROOT / "docs/research/FEEDBACK_REMEDIATION_REVIEW_V1.md",
    ROOT / "docs/research/UNIFIED_PIPELINE_AUDIT_V1.md",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feedback-dir", type=Path, required=True)
    parser.add_argument("--prior-model-dir", type=Path, required=True)
    parser.add_argument("--semantic-summary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--feedback-package",
        type=Path,
        default=None,
        help="optional compact feedback ZIP to attach to the handoff",
    )
    parser.add_argument(
        "--e2e-receipts",
        type=Path,
        default=None,
        help="optional independent E2E receipt JSONL for source-closure diagnostics",
    )
    parser.add_argument(
        "--pipeline-integrity",
        type=Path,
        default=None,
        help="optional unified-flow integrity JSON",
    )
    parser.add_argument("--expected-question-count", type=int, default=1012)
    parser.add_argument(
        "--research-doc",
        type=Path,
        action="append",
        default=None,
        help="repeatable; defaults to the four research review documents",
    )
    args = parser.parse_args()
    docs = args.research_doc if args.research_doc is not None else DEFAULT_RESEARCH_DOCS
    summary = build_population_feedback_breakthrough(
        feedback_dir=args.feedback_dir,
        prior_model_dir=args.prior_model_dir,
        semantic_summary_path=args.semantic_summary,
        output_dir=args.output_dir,
        expected_question_count=args.expected_question_count,
        research_docs=docs,
        feedback_package_path=args.feedback_package,
        e2e_receipts_path=args.e2e_receipts,
        pipeline_integrity_path=args.pipeline_integrity,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
