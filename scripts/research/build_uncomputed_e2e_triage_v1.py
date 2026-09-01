#!/usr/bin/env python3
"""Build a research-only root-cause and repair queue for the locked E2E run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.uncomputed_e2e_triage import build_uncomputed_e2e_triage


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plans", type=Path, required=True)
    parser.add_argument("--taxonomy", type=Path, required=True)
    parser.add_argument("--routing-status", type=Path, required=True)
    parser.add_argument("--hybrid-review-queue", type=Path, required=True)
    parser.add_argument("--period-packets", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--evidence-bindings", type=Path, required=True)
    parser.add_argument("--certificates", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    args = parser.parse_args()
    result = build_uncomputed_e2e_triage(
        plans_path=args.plans,
        taxonomy_path=args.taxonomy,
        routing_status_path=args.routing_status,
        hybrid_review_queue_path=args.hybrid_review_queue,
        period_packets_path=args.period_packets,
        bindings_path=args.bindings,
        execution_path=args.execution,
        evidence_bindings_path=args.evidence_bindings,
        certificates_path=args.certificates,
        output_dir=args.output_dir,
        expected_question_count=args.expected_question_count,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
