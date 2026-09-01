#!/usr/bin/env python3
"""Build a value-blind source-gap audit for current missing direct routes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.direct_lookup_source_gap_audit import build_direct_lookup_source_gap_audit


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in (
        "triage",
        "plans",
        "base-route-overlay",
        "row-review-queue",
        "structured-tables",
        "evidence-context",
        "full-adapter-artifact-dir",
        "materialization-artifact-dir",
        "output-dir",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    args = parser.parse_args()
    print(
        json.dumps(
            build_direct_lookup_source_gap_audit(
                triage_path=args.triage,
                plans_path=args.plans,
                base_route_overlay_path=args.base_route_overlay,
                row_review_queue_path=args.row_review_queue,
                structured_tables_path=args.structured_tables,
                evidence_context_path=args.evidence_context,
                full_adapter_artifact_dir=args.full_adapter_artifact_dir,
                materialization_artifact_dir=args.materialization_artifact_dir,
                output_dir=args.output_dir,
                expected_question_count=args.expected_question_count,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
