#!/usr/bin/env python3
"""Build a value-blind source audit for E2E composition-graph blockers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.composition_operand_source_gap_audit import build_composition_operand_source_gap_audit


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in (
        "triage",
        "plans",
        "row-review-queue",
        "full-corpus-artifact-dir",
        "structured-tables",
        "evidence-context",
        "evidence-context-manifest",
        "output-dir",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    parser.add_argument("--target-question-ids", type=Path)
    parser.add_argument("--minimum-row-jaccard", type=float, default=0.9)
    parser.add_argument("--minimum-row-margin", type=float, default=0.2)
    args = parser.parse_args()
    print(
        json.dumps(
            build_composition_operand_source_gap_audit(
                triage_path=args.triage,
                target_question_ids_path=args.target_question_ids,
                plans_path=args.plans,
                row_review_queue_path=args.row_review_queue,
                full_corpus_artifact_dir=args.full_corpus_artifact_dir,
                structured_tables_path=args.structured_tables,
                evidence_context_path=args.evidence_context,
                evidence_context_manifest_path=args.evidence_context_manifest,
                output_dir=args.output_dir,
                expected_question_count=args.expected_question_count,
                minimum_row_jaccard=args.minimum_row_jaccard,
                minimum_row_margin=args.minimum_row_margin,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
