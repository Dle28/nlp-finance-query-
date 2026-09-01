#!/usr/bin/env python3
"""Build the question-intrinsic ViFinQA map and frozen-baseline census."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.dataset_map import build_dataset_research_map


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--baseline-certificates", type=Path, required=True)
    parser.add_argument("--entity-aliases", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    args = parser.parse_args()
    summary = build_dataset_research_map(
        questions=args.questions,
        baseline_certificates=args.baseline_certificates,
        entity_aliases=args.entity_aliases,
        output_dir=args.output_dir,
        expected_question_count=args.expected_question_count,
    )
    print(json.dumps({
        "status": "BUILD_COMPLETE",
        "question_count": summary["question_count"],
        "candidate_category_count": summary["candidate_category_count"],
        "baseline_status_counts": summary["baseline_overall"]["status_counts"],
        "output_dir": str(args.output_dir),
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
